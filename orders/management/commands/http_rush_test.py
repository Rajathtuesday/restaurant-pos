"""
HTTP-level multi-tenant "dinner rush" load test.

Unlike load_test.py (which calls the service layer directly, bypassing HTTP
entirely), this fires REAL HTTP requests at a running server -- exercising
gunicorn/WSGI, nginx if it's in front, and the full middleware stack, not
just the DB layer. That's the only way to answer "does the box hold up,"
as opposed to "is the code correct under concurrency" (load_test.py already
answers that one).

Seeds N throwaway tenants (each with an outlet, a counter QR token, a menu),
then fires concurrent guest-style counter orders -- POST /create-order/ with
a table_token, no login required, matching how a real QR/counter guest
orders -- spread across all N tenants at once, simulating several small
restaurants each getting hit by their own rush simultaneously.

HONEST CAVEAT #1: /create-order/ is IP-rate-limited (20/min per IP) by
design, to stop cart-spam. Every simulated "guest" this script fires really
comes from this one machine's IP -- so a burst well past ~20 total requests
WILL legitimately start getting HTTP_429 back, same as a real attacker would.
That's the rate limiter doing its job correctly, not a capacity failure --
if you see HTTP_429 dominate the error breakdown, that's expected at
anything above --tenants * --requests-per-tenant ~= 20 run in a tight burst,
not a sign the server is struggling.

HONEST CAVEAT #2, worth reading before trusting any number this prints: this
only tells you about the capacity of whatever machine the SERVER is running
on. If you point --host at a server running on this same machine, the
numbers reflect this machine, not your production EC2 box. If you want real
production capacity numbers, either run this command directly on that box
(against its own localhost), or run it from a separate machine while
watching that box's own CPU/memory (`top`, `free -h`) yourself -- this tool
cannot see resources on a machine it isn't running on.

Usage:
    python manage.py http_rush_test --host http://127.0.0.1:8000
    python manage.py http_rush_test --tenants 10 --requests-per-tenant 20 --workers 30
    python manage.py http_rush_test --host http://10.0.0.5:8000 --allow-remote
"""
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from tenants.models import Tenant, Outlet

from ._loadtest_common import (
    RunLogger, ResourceSampler, db_snapshot, format_resource_line,
    cleanup_tenant, is_safe_host,
)

RUSH_SLUG_PREFIX = "loadtest-rush-"


class Command(BaseCommand):
    help = "HTTP-level multi-tenant rush test -- fires real requests at a running server."

    def add_arguments(self, parser):
        parser.add_argument("--host", default="http://127.0.0.1:8000",
                            help="Server to hit (default: local dev server).")
        parser.add_argument("--tenants", type=int, default=8,
                            help="Number of throwaway tenants to simulate simultaneously.")
        parser.add_argument("--requests-per-tenant", type=int, default=15,
                            help="Guest orders fired per tenant.")
        parser.add_argument("--workers", type=int, default=20,
                            help="Total concurrent threads across all tenants.")
        parser.add_argument("--keep", action="store_true",
                            help="Keep the throwaway tenants instead of cleaning up.")
        parser.add_argument("--allow-remote", action="store_true",
                            help="Required to target a host that isn't localhost/private -- "
                                 "safety guard against accidentally hitting production.")
        parser.add_argument("--log-dir", default="loadtest_logs",
                            help="Directory for the timestamped run log (default: loadtest_logs/).")

    # ------------------------------------------------------------------ setup
    def _setup_tenant(self, i):
        slug = f"{RUSH_SLUG_PREFIX}{i}"
        tenant, _ = Tenant.objects.get_or_create(slug=slug, defaults={"name": f"LoadTest Rush {i}"})
        outlet, _ = Outlet.objects.get_or_create(tenant=tenant, name="Rush Outlet")

        category, _ = MenuCategory.objects.get_or_create(
            tenant=tenant, outlet=outlet, name="Rush Menu"
        )
        items = list(MenuItem.objects.filter(tenant=tenant, outlet=outlet))
        if not items:
            for name, price in [("Burger", 200), ("Fries", 120), ("Coke", 80)]:
                MenuItem.objects.create(
                    tenant=tenant, outlet=outlet, category=category, name=name, price=price,
                )
            items = list(MenuItem.objects.filter(tenant=tenant, outlet=outlet))

        return tenant, str(outlet.qr_token), items

    # -------------------------------------------------------------- request
    def _guest_order(self, base_url, qr_token, item_ids, n):
        session = requests.Session()
        lats, errs = [], {}

        # A real guest's browser loads the menu page before ordering, which
        # is what actually sets the csrftoken cookie -- create-order isn't
        # csrf_exempt, so skipping this step gets every request rejected
        # 403 regardless of load, which would look like a false capacity
        # failure. Mirror that real flow instead of faking a cookie.
        try:
            session.get(f"{base_url}/menu/{qr_token}/", timeout=10)
        except requests.RequestException as exc:
            return [], {type(exc).__name__: n}
        csrf_token = session.cookies.get("csrftoken")

        for _ in range(n):
            cart = [
                {"id": random.choice(item_ids), "quantity": random.randint(1, 3), "modifiers": []}
                for _ in range(random.randint(1, 3))
            ]
            payload = {
                "cart": cart,
                "table_token": qr_token,
                "source": "web",
                "customer_phone": f"9{random.randint(0, 999999999):09d}",
            }
            start = time.perf_counter()
            try:
                resp = session.post(
                    f"{base_url}/create-order/", json=payload, timeout=15,
                    headers={"X-CSRFToken": csrf_token} if csrf_token else {},
                )
                if resp.status_code == 200:
                    lats.append(time.perf_counter() - start)
                else:
                    key = f"HTTP_{resp.status_code}"
                    errs[key] = errs.get(key, 0) + 1
            except requests.RequestException as exc:
                key = type(exc).__name__
                errs[key] = errs.get(key, 0) + 1
        return lats, errs

    # ------------------------------------------------------------------- main
    def handle(self, *args, **opts):
        host = opts["host"].rstrip("/")
        if not opts["allow_remote"] and not is_safe_host(host):
            raise CommandError(
                f"'{host}' doesn't look like localhost or a private address. Refusing to run "
                "without --allow-remote -- this guard exists specifically so this command can "
                "never accidentally fire concurrent load at production. If you really mean it, "
                "re-run with --allow-remote."
            )

        log = RunLogger(self.stdout.write, opts["log_dir"], "http_rush_test")
        log.write(f"Target: {host}")
        log.write(
            "Note: /create-order/ rate-limits at 20/min per source IP. Every simulated "
            "guest here shares this machine's real IP, so HTTP_429 in the error breakdown "
            "below past ~20 total requests is the rate limiter correctly doing its job, "
            "not a server capacity problem."
        )
        if "postgresql" not in connection.vendor:
            log.write(self.style.WARNING(
                "Local DB backend is SQLite -- fine for exercising this script itself, "
                "but if --host points at a server using SQLite too, treat any numbers "
                "here the same way load_test.py warns about: not representative.\n"
            ))

        n_tenants = opts["tenants"]
        total = n_tenants * opts["requests_per_tenant"]
        workers = opts["workers"]

        log.write(f"Setting up {n_tenants} throwaway tenants...")
        tenants, qr_tokens, item_ids_per_tenant = [], [], []
        for i in range(n_tenants):
            tenant, qr_token, items = self._setup_tenant(i)
            tenants.append(tenant)
            qr_tokens.append(qr_token)
            item_ids_per_tenant.append([it.id for it in items])

        # Quick reachability check before firing real load.
        try:
            requests.get(host, timeout=5)
        except requests.RequestException as exc:
            log.write(self.style.ERROR(f"Could not reach {host}: {exc}"))
            if not opts["keep"]:
                for t in tenants:
                    cleanup_tenant(t)
            log.close()
            return

        log.write(self.style.MIGRATE_HEADING(
            f"\nHTTP rush: {total} guest orders across {n_tenants} tenants, "
            f"{workers} concurrent workers, target {host}"
        ))

        db_before = db_snapshot()
        sampler = ResourceSampler().start()
        latencies, errors = [], {}
        wall_start = time.perf_counter()

        jobs = []
        per_tenant = opts["requests_per_tenant"]
        for i in range(n_tenants):
            jobs.append((qr_tokens[i], item_ids_per_tenant[i], per_tenant))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(self._guest_order, host, qr_token, item_ids, n)
                for qr_token, item_ids, n in jobs
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
        log.write(format_resource_line("  resources (this machine, see caveat above)", res))
        if db_before and db_after:
            log.write(f"  db connections: {db_before['active_connections']} -> {db_after['active_connections']}  "
                       f"waiting locks (after): {db_after['waiting_locks']}")

        error_rate = (failed / total * 100) if total else 0
        if ok:
            s = sorted(latencies)
            p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))] * 1000
            if p95 > 2000 or error_rate > 1:
                log.write(self.style.WARNING(
                    f"\n  p95={p95:.0f}ms, error rate={error_rate:.1f}% -- past the rough "
                    "'comfortable' ceiling (p95 < ~1-2s, errors < ~1%) suggested in the original plan."
                ))
            else:
                log.write(self.style.SUCCESS(
                    f"\n  p95={p95:.0f}ms, error rate={error_rate:.1f}% -- comfortably under "
                    "the rough ceiling from the original plan."
                ))

        if opts["keep"]:
            log.write(self.style.WARNING(f"\n--keep set: leaving {n_tenants} rush tenants in place."))
        else:
            log.write(f"\nCleaning up {n_tenants} rush tenants...")
            for t in tenants:
                cleanup_tenant(t)
            log.write(self.style.SUCCESS("Cleanup complete."))

        log.write(self.style.SUCCESS("\nHTTP rush test finished."))
        log.write(f"\nFull log saved to: {log.path}")
        log.close()

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
