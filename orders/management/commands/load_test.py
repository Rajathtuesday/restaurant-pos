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
    python manage.py load_test --soak-minutes 30                   # + a sustained Phase 4

A fourth, OPT-IN phase (--soak-minutes, off by default) holds a steady
number of workers running the same order lifecycle continuously for N
minutes, checkpointing throughput/memory/DB state every --soak-checkpoint-
seconds. This is deliberately DB-level, not HTTP: /create-order/ rate-limits
at 20/min per IP, which would make an HTTP-level soak test measure the rate
limiter for 29 of every 30 minutes, not the app -- see http_rush_test.py for
the HTTP-level (burst, not sustained) test instead.

By default it creates (and afterwards deletes) a dedicated "LoadTest" tenant so
it never touches real restaurant data.
"""
import random
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import connection
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tenants.models import Tenant, Outlet

from orders.services.order_service import get_or_create_open_order, add_items_to_order
from kitchen.services.kot_service import create_kot
from orders.services.payment_service import process_payment

from ._loadtest_common import (
    RunLogger, ResourceSampler, db_snapshot, format_resource_line, cleanup_tenant,
)

LOADTEST_SLUG = "loadtest-tenant"


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
        parser.add_argument("--no-reservation-race", action="store_true",
                            help="Skip the reservation-booking race probe.")
        parser.add_argument("--log-dir", default="loadtest_logs",
                            help="Directory for the timestamped run log (default: loadtest_logs/).")
        parser.add_argument("--soak-minutes", type=int, default=0,
                            help="Run an additional Phase 4: N minutes of sustained load, "
                                 "checkpointed periodically. 0 (default) = skip this phase.")
        parser.add_argument("--soak-workers", type=int, default=0,
                            help="Workers for the soak phase (default: same as --workers).")
        parser.add_argument("--soak-checkpoint-seconds", type=int, default=60,
                            help="How often the soak phase logs a throughput/memory checkpoint.")

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
        log = RunLogger(self.stdout.write, opts["log_dir"], "load_test")

        if "sqlite" in connection.vendor:
            log.write(self.style.WARNING(
                "DB backend is SQLite — its single writer lock serialises all writes, "
                "so concurrency numbers here are NOT representative. Point DB_* env vars "
                "at Postgres for a real result.\n"
            ))

        workers = opts["workers"]
        per_worker = max(1, opts["orders"] // workers)
        total = per_worker * workers   # actual total after even split across workers

        log.write(f"Setting up LoadTest tenant ({workers} tables)...")
        tenant, outlet, user, tables, items = self._setup(workers)

        # ---------------- Phase 1: throughput ----------------
        log.write(self.style.MIGRATE_HEADING(
            f"\nPhase 1 — throughput: {total} order lifecycles, "
            f"{workers} workers x {per_worker} each (one table per worker)"
        ))
        db_before = db_snapshot()
        sampler = ResourceSampler().start()
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
        res = sampler.stop()
        db_after = db_snapshot()

        ok = len(latencies)
        failed = sum(errors.values())
        log.write(f"  completed : {ok}/{total}   failed: {failed}")
        log.write(f"  wall time : {wall:.2f}s")
        if ok:
            log.write(f"  throughput: {ok / wall:.1f} orders/sec")
            self._latency_line(log, "  latency   ", latencies)
        if errors:
            log.write(self.style.WARNING("  error breakdown:"))
            for name, count in sorted(errors.items(), key=lambda x: -x[1]):
                log.write(f"    {name}: {count}")
        log.write(format_resource_line("  resources ", res))
        if db_before and db_after:
            log.write(f"  db connections: {db_before['active_connections']} -> {db_after['active_connections']}  "
                       f"waiting locks (after): {db_after['waiting_locks']}")

        # ---------------- Phase 2: single-table race probe ----------------
        if not opts["no_race"]:
            rw = opts["race_workers"]
            log.write(self.style.MIGRATE_HEADING(
                f"\nPhase 2 — race probe: {rw} threads open an order on ONE table at once"
            ))
            race_table = Table.objects.create(tenant=tenant, outlet=outlet, name="LT-RACE")

            def _race_open():
                try:
                    o = get_or_create_open_order(user, race_table)
                    return o.id
                finally:
                    connection.close()

            sampler = ResourceSampler().start()
            with ThreadPoolExecutor(max_workers=rw) as pool:
                order_ids = [f.result() for f in [pool.submit(_race_open) for _ in range(rw)]]
            res = sampler.stop()

            distinct = set(order_ids)
            open_rows = Order.objects.filter(
                tenant=tenant, outlet=outlet, table=race_table, status="open"
            ).count()
            log.write(f"  {rw} concurrent opens -> {len(distinct)} distinct order id(s)")
            log.write(f"  open orders actually in DB for that table: {open_rows}")
            log.write(format_resource_line("  resources", res))
            if open_rows == 1 and len(distinct) == 1:
                log.write(self.style.SUCCESS(
                    "  PASS: the unique-open-order-per-table guard held under the race."
                ))
            else:
                log.write(self.style.ERROR(
                    "  FAIL: more than one open order was created for a single table!"
                ))

        # ---------------- Phase 3: reservation race probe ----------------
        if not opts["no_reservation_race"]:
            rw = opts["race_workers"]
            log.write(self.style.MIGRATE_HEADING(
                f"\nPhase 3 — reservation race probe: {rw} threads book ONE table/time-slot at once"
            ))
            res_table = Table.objects.create(tenant=tenant, outlet=outlet, name="LT-RES-RACE")
            res_time = timezone.now() + timedelta(days=1)
            res_time_str = res_time.strftime("%Y-%m-%dT%H:%M")

            from crm.views import create_reservation
            factory = RequestFactory()

            # In-memory only (never .save()'d) -- bypasses feature_required's
            # "does this tenant have the 'reservations' feature" check, same
            # as tenant_required's subdomain check, which is already a no-op
            # here since RequestFactory requests never run through middleware.
            user.is_superuser = True

            import json as _json

            def _race_book(i):
                body = _json.dumps({
                    "phone": f"9{i:09d}",
                    "name": f"LoadTest Guest {i}",
                    "table_id": res_table.id,
                    "reservation_time": res_time_str,
                    "guests": 2,
                })
                request = factory.post(
                    "/crm/reservations/create/",
                    data=body,
                    content_type="application/json",
                )
                request.user = user
                try:
                    response = create_reservation(request)
                    return response.status_code
                finally:
                    connection.close()

            sampler = ResourceSampler().start()
            with ThreadPoolExecutor(max_workers=rw) as pool:
                statuses = [f.result() for f in [pool.submit(_race_book, i) for i in range(rw)]]
            res = sampler.stop()

            successes = statuses.count(200)
            conflicts = statuses.count(409)
            from crm.models import Reservation
            confirmed_rows = Reservation.objects.filter(
                tenant=tenant, outlet=outlet, table=res_table,
                status__in=["pending", "confirmed"],
            ).count()
            log.write(f"  {rw} concurrent bookings -> {successes} succeeded (200), {conflicts} rejected (409)")
            log.write(f"  reservation rows actually in DB for that table/slot: {confirmed_rows}")
            log.write(format_resource_line("  resources", res))
            if successes == 1 and confirmed_rows == 1:
                log.write(self.style.SUCCESS(
                    "  PASS: exactly one booking succeeded for the contested table/slot."
                ))
            else:
                log.write(self.style.ERROR(
                    "  FAIL: expected exactly 1 success and 1 DB row -- double-booking risk!"
                ))

        # ---------------- Phase 4: soak test (opt-in) ----------------
        if opts["soak_minutes"] > 0:
            soak_minutes = opts["soak_minutes"]
            soak_workers = opts["soak_workers"] or workers
            checkpoint_s = opts["soak_checkpoint_seconds"]
            log.write(self.style.MIGRATE_HEADING(
                f"\nPhase 4 — soak test: {soak_workers} workers, sustained for {soak_minutes} min, "
                f"checkpoint every {checkpoint_s}s"
            ))
            soak_tables = [
                Table.objects.create(tenant=tenant, outlet=outlet, name=f"LT-SOAK{i}")
                for i in range(soak_workers)
            ]
            stop_event = threading.Event()
            counts_lock = threading.Lock()
            counts = {"completed": 0, "failed": 0}

            def _soak_loop(table):
                try:
                    while not stop_event.is_set():
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
                            with counts_lock:
                                counts["completed"] += 1
                        except Exception:
                            with counts_lock:
                                counts["failed"] += 1
                finally:
                    connection.close()

            threads = [threading.Thread(target=_soak_loop, args=(t,), daemon=True) for t in soak_tables]
            for th in threads:
                th.start()

            soak_start = time.perf_counter()
            end_at = soak_start + soak_minutes * 60
            mem_series = []
            prev_completed = 0
            while True:
                now = time.perf_counter()
                remaining = end_at - now
                if remaining <= 0:
                    break
                sampler = ResourceSampler().start()
                time.sleep(min(checkpoint_s, remaining))
                res = sampler.stop()
                elapsed = time.perf_counter() - soak_start
                with counts_lock:
                    c, f = counts["completed"], counts["failed"]
                db = db_snapshot()
                delta = c - prev_completed
                prev_completed = c
                db_bit = (f"  db_conns={db['active_connections']} waiting_locks={db['waiting_locks']}"
                          if db else "")
                log.write(
                    f"  t={elapsed / 60:.1f}min  completed={c} (+{delta}) failed={f}  "
                    f"{format_resource_line('mem/cpu', res)}{db_bit}"
                )
                if res.get("available") and res.get("samples", 0):
                    mem_series.append((elapsed, res["mem_mb_mean"]))

            stop_event.set()
            for th in threads:
                th.join(timeout=10)

            log.write(f"  soak finished: {counts['completed']} completed, {counts['failed']} failed "
                      f"over {soak_minutes} min")
            if len(mem_series) >= 4:
                half = len(mem_series) // 2
                first_half_mean = statistics.mean(m for _, m in mem_series[:half])
                second_half_mean = statistics.mean(m for _, m in mem_series[half:])
                growth_pct = ((second_half_mean - first_half_mean) / first_half_mean * 100) if first_half_mean else 0
                still_climbing = mem_series[-1][1] > mem_series[-2][1]
                if growth_pct > 20 and still_climbing:
                    log.write(self.style.WARNING(
                        f"  WATCH: mean memory grew {growth_pct:.0f}% from first half to second half "
                        f"of the soak ({first_half_mean:.0f}MB -> {second_half_mean:.0f}MB) and was still "
                        "climbing at the end -- worth a longer soak to confirm this is a real leak "
                        "and not just warm-up/cache fill."
                    ))
                else:
                    log.write(self.style.SUCCESS(
                        f"  Memory looked stable: {first_half_mean:.0f}MB -> {second_half_mean:.0f}MB "
                        f"({growth_pct:+.0f}%) across the soak."
                    ))
            else:
                log.write("  (too few checkpoints to judge a memory trend -- use a longer --soak-minutes "
                           "or shorter --soak-checkpoint-seconds)")

        # ---------------- cleanup ----------------
        if opts["keep"]:
            log.write(self.style.WARNING(
                f"\n--keep set: leaving LoadTest tenant '{tenant.slug}' and its data in place."
            ))
        else:
            log.write("\nCleaning up LoadTest tenant...")
            cleanup_tenant(tenant)
            log.write(self.style.SUCCESS("Cleanup complete."))

        log.write(self.style.SUCCESS("\nLoad test finished."))
        log.write(f"\nFull log saved to: {log.path}")
        log.close()

    # -------------------------------------------------------------- helpers
    def _latency_line(self, log, label, latencies):
        s = sorted(latencies)

        def pct(p):
            if not s:
                return 0.0
            idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
            return s[idx] * 1000

        log.write(
            f"{label}: p50={pct(50):.0f}ms  p95={pct(95):.0f}ms  "
            f"p99={pct(99):.0f}ms  max={max(s) * 1000:.0f}ms  "
            f"mean={statistics.mean(s) * 1000:.0f}ms"
        )
