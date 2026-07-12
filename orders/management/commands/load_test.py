"""
Concurrency load test for the Rasova POS order pipeline.

Runs the REAL service layer (get_or_create_open_order -> add_items_to_order ->
create_kot -> process_payment) under concurrent threads and reports throughput,
latency percentiles, and an error breakdown. A second phase hammers a single
table to prove the unique-open-order-per-table guard holds under a race.

This is a DB-level concurrency test (not an HTTP load test) — it exercises the
select_for_update locks, the daily counters, and the payment path where the real
races live. Run it against Postgres; on SQLite the writer lock serialises
everything and the numbers are meaningless (the command warns about this).

Usage:
    python manage.py load_test --orders 300 --workers 16
    python manage.py load_test --orders 300 --workers 16 --keep   # skip cleanup
    python manage.py load_test --race-workers 40                   # race probe size

By default it creates (and afterwards deletes) a dedicated "LoadTest" tenant so
it never touches real restaurant data.
"""
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand
from django.db import connection

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tenants.models import Tenant, Outlet

from orders.services.order_service import get_or_create_open_order, add_items_to_order
from orders.services.kot_service import create_kot
from orders.services.payment_service import process_payment

LOADTEST_SLUG = "loadtest-tenant"


def _cleanup_tenant(tenant):
    """Delete a tenant and everything under it, honoring the PROTECT FK chain
    (Payment.order and Order.tenant are both on_delete=PROTECT, so payments must
    go before orders, and orders before the tenant)."""
    from orders.models import Order, Payment, Refund
    Refund.objects.filter(order__tenant=tenant).delete()
    Payment.objects.filter(order__tenant=tenant).delete()
    Order.objects.filter(tenant=tenant).delete()
    Tenant.objects.filter(pk=tenant.pk).delete()


class Command(BaseCommand):
    help = "Concurrency load test for the order pipeline (throughput + race probe)."

    def add_arguments(self, parser):
        parser.add_argument("--orders", type=int, default=200,
                            help="Total order lifecycles to run in the throughput phase.")
        parser.add_argument("--workers", type=int, default=12,
                            help="Concurrent worker threads.")
        parser.add_argument("--race-workers", type=int, default=30,
                            help="Threads that simultaneously hit ONE table in the race probe.")
        parser.add_argument("--keep", action="store_true",
                            help="Keep the LoadTest tenant + orders instead of cleaning up.")
        parser.add_argument("--no-race", action="store_true",
                            help="Skip the single-table race probe.")

    # ------------------------------------------------------------------ setup
    def _setup(self, n_tables):
        tenant, _ = Tenant.objects.get_or_create(
            slug=LOADTEST_SLUG, defaults={"name": "LoadTest"}
        )
        outlet, _ = Outlet.objects.get_or_create(tenant=tenant, name="LoadTest Outlet")
        user, created = User.objects.get_or_create(
            username="loadtest_owner",
            defaults={"role": "owner", "tenant": tenant, "outlet": outlet},
        )
        if created:
            user.set_password("loadtest")
            user.tenant, user.outlet, user.role = tenant, outlet, "owner"
            user.save()

        category, _ = MenuCategory.objects.get_or_create(
            tenant=tenant, outlet=outlet, name="LoadTest Menu"
        )
        items = list(MenuItem.objects.filter(tenant=tenant, outlet=outlet))
        if not items:
            for name, price in [("Burger", 200), ("Fries", 120), ("Coke", 80),
                                ("Pizza", 300), ("Wrap", 150)]:
                MenuItem.objects.create(
                    tenant=tenant, outlet=outlet, category=category,
                    name=name, price=price,
                )
            items = list(MenuItem.objects.filter(tenant=tenant, outlet=outlet))

        existing = list(Table.objects.filter(tenant=tenant, outlet=outlet))
        for i in range(len(existing), n_tables):
            existing.append(Table.objects.create(
                tenant=tenant, outlet=outlet, name=f"LT{i + 1}"
            ))
        return tenant, outlet, user, existing[:n_tables], items

    # -------------------------------------------------------------- lifecycle
    def _worker(self, user, table, items, n):
        """Run n full order lifecycles on ONE dedicated table, sequentially.

        Each worker owns its own table so there's no artificial same-table
        contention — the real concurrency stress is the shared per-outlet daily
        counters (order number, KOT number) that every worker increments under
        select_for_update. Returns (latencies, errors)."""
        lats, errs = [], {}
        try:
            for _ in range(n):
                start = time.perf_counter()
                try:
                    order = get_or_create_open_order(user, table)
                    cart = [
                        {"id": random.choice(items).id, "quantity": random.randint(1, 3), "modifiers": []}
                        for _ in range(random.randint(1, 4))
                    ]
                    add_items_to_order(user, order, cart)
                    create_kot(user, order)
                    order.refresh_from_db()
                    process_payment(order, "cash", order.grand_total)
                    lats.append(time.perf_counter() - start)
                except Exception as exc:  # noqa: BLE001 - capture class name
                    key = type(exc).__name__
                    errs[key] = errs.get(key, 0) + 1
        finally:
            connection.close()  # thread-local connection — release it
        return lats, errs

    # ------------------------------------------------------------------- main
    def handle(self, *args, **opts):
        if "sqlite" in connection.vendor:
            self.stdout.write(self.style.WARNING(
                "DB backend is SQLite — its single writer lock serialises all writes, "
                "so concurrency numbers here are NOT representative. Point DB_* env vars "
                "at Postgres for a real result.\n"
            ))

        workers = opts["workers"]
        per_worker = max(1, opts["orders"] // workers)
        total = per_worker * workers   # actual total after even split across workers

        self.stdout.write(f"Setting up LoadTest tenant ({workers} tables)...")
        tenant, outlet, user, tables, items = self._setup(workers)

        # ---------------- Phase 1: throughput ----------------
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nPhase 1 — throughput: {total} order lifecycles, "
            f"{workers} workers x {per_worker} each (one table per worker)"
        ))
        latencies, errors = [], {}
        wall_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(self._worker, user, tables[w], items, per_worker)
                for w in range(workers)
            ]
            for fut in as_completed(futures):
                lats, errs = fut.result()
                latencies.extend(lats)
                for k, v in errs.items():
                    errors[k] = errors.get(k, 0) + v
        wall = time.perf_counter() - wall_start

        ok = len(latencies)
        failed = sum(errors.values())
        self.stdout.write(f"  completed : {ok}/{total}   failed: {failed}")
        self.stdout.write(f"  wall time : {wall:.2f}s")
        if ok:
            self.stdout.write(f"  throughput: {ok / wall:.1f} orders/sec")
            self._latency_line("  latency   ", latencies)
        if errors:
            self.stdout.write(self.style.WARNING("  error breakdown:"))
            for name, count in sorted(errors.items(), key=lambda x: -x[1]):
                self.stdout.write(f"    {name}: {count}")

        # ---------------- Phase 2: single-table race probe ----------------
        if not opts["no_race"]:
            rw = opts["race_workers"]
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\nPhase 2 — race probe: {rw} threads open an order on ONE table at once"
            ))
            race_table = Table.objects.create(tenant=tenant, outlet=outlet, name="LT-RACE")

            def _race_open():
                try:
                    o = get_or_create_open_order(user, race_table)
                    return o.id
                finally:
                    connection.close()

            with ThreadPoolExecutor(max_workers=rw) as pool:
                order_ids = [f.result() for f in [pool.submit(_race_open) for _ in range(rw)]]

            distinct = set(order_ids)
            open_rows = Order.objects.filter(
                tenant=tenant, outlet=outlet, table=race_table, status="open"
            ).count()
            self.stdout.write(f"  {rw} concurrent opens -> {len(distinct)} distinct order id(s)")
            self.stdout.write(f"  open orders actually in DB for that table: {open_rows}")
            if open_rows == 1 and len(distinct) == 1:
                self.stdout.write(self.style.SUCCESS(
                    "  PASS: the unique-open-order-per-table guard held under the race."
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    "  FAIL: more than one open order was created for a single table!"
                ))

        # ---------------- cleanup ----------------
        if opts["keep"]:
            self.stdout.write(self.style.WARNING(
                f"\n--keep set: leaving LoadTest tenant '{tenant.slug}' and its data in place."
            ))
        else:
            self.stdout.write("\nCleaning up LoadTest tenant...")
            _cleanup_tenant(tenant)
            self.stdout.write(self.style.SUCCESS("Cleanup complete."))

        self.stdout.write(self.style.SUCCESS("\nLoad test finished."))

    # -------------------------------------------------------------- helpers
    def _latency_line(self, label, latencies):
        s = sorted(latencies)

        def pct(p):
            if not s:
                return 0.0
            idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
            return s[idx] * 1000

        self.stdout.write(
            f"{label}: p50={pct(50):.0f}ms  p95={pct(95):.0f}ms  "
            f"p99={pct(99):.0f}ms  max={max(s) * 1000:.0f}ms  "
            f"mean={statistics.mean(s) * 1000:.0f}ms"
        )
