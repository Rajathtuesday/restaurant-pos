"""
Pub-night simulation for Rasova.

load_test.py already proves the order pipeline under a QSR-shaped pattern
(one order, immediate payment, on a loop). A pub doesn't behave like that --
this runs a second, separate scenario: tables open real tabs, add rounds of
food AND alcohol over hours, close out through every real payment path
(normal, complimentary, split, manager bypass), and the run deliberately
injects the unhappy paths too (cancelled items, an item going unavailable,
a stale QR re-order, hourly spillage) -- not just the clean case.

Uses the REAL service layer and REAL views (via RequestFactory for the
view-only endpoints, same as load_test.py's Phase 3 reservation probe),
against a dedicated throwaway tenant. Menu prices/GST/recipes are the
hand-verified set from
    md_files/2026-08-30-pub-night-simulation-plan.html

Usage:
    python manage.py pub_night_test --hours 0.1   # dry run, ~6 minutes -- ALWAYS run this first
    python manage.py pub_night_test --hours 8      # the real overnight run
    python manage.py pub_night_test --hours 8 --keep

Timing note: round/tab/spillage cadence is expressed as a FRACTION of
--hours, not fixed wall-clock minutes. That's deliberate -- it means a
0.1-hour dry run exercises the same relative mix of rounds-per-tab,
table-turnovers, and spillage events as an 8-hour run, compressed, so the
dry run is an actual smoke test of every code path instead of mostly idle.
"""
import json as _json
import random
import statistics
import threading
import time
import uuid
from decimal import Decimal

from django.contrib.auth.models import AnonymousUser
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import F
from django.test import RequestFactory
from django.utils import timezone

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tenants.models import Tenant, Outlet
from setup.models import PaymentConfig
from shifts.models import CashSession
from inventory.models import InventoryItem, Supplier

from orders.services.order_service import get_or_create_open_order, add_items_to_order
from kitchen.services.kot_service import create_kot
from orders.services.void_service import void_order_item
from orders.views.billing_views import create_order
from orders.views.payment_views import pay_order, split_pay
from orders.views.discount_views import log_bypass

from ._loadtest_common import (
    RunLogger, ResourceSampler, db_snapshot, format_resource_line, cleanup_tenant,
)

PUBTEST_SLUG = "pubtest-tenant"

# (name, price, gst_percentage, stock_item_name, qty_per_serving) -- same
# shape as ALCOHOL_ITEMS below, so every menu item (food or drink) consumes
# a real ingredient, tracked to the one input that actually runs out for
# that dish -- not a full multi-ingredient bill of materials, same
# deliberate simplification as the alcohol recipes.
FOOD_ITEMS = [
    ("Chicken Wings", 280, 5, "Chicken Wings (raw)", 0.25),
    ("Nachos with Salsa", 220, 5, "Tortilla Chips", 0.15),
    ("Loaded Fries", 180, 5, "Potatoes", 0.20),
    ("Paneer Tikka", 260, 5, "Paneer", 0.20),
    ("Chicken Tikka", 300, 5, "Chicken Breast", 0.20),
    ("Onion Rings", 150, 5, "Onions", 0.15),
    ("Garlic Bread", 140, 5, "Bread Loaf", 1),
    ("Veg Platter", 320, 5, "Mixed Vegetables", 0.30),
    ("Soda / Coke", 80, 5, "Soda Cans", 1),
    ("Fresh Lime Soda", 90, 5, "Lemons", 2),
    ("Iced Tea", 110, 5, "Tea Leaves", 10),
    ("Virgin Mojito", 180, 5, "Mint Leaves", 10),
]

# (name, unit, starting_stock)
FOOD_STOCK = [
    ("Chicken Wings (raw)", "kg", 15),
    ("Tortilla Chips", "kg", 10),
    ("Potatoes", "kg", 15),
    ("Paneer", "kg", 8),
    ("Chicken Breast", "kg", 12),
    ("Onions", "kg", 10),
    ("Bread Loaf", "pcs", 40),
    ("Mixed Vegetables", "kg", 12),
    ("Soda Cans", "pcs", 150),
    ("Lemons", "pcs", 100),
    ("Tea Leaves", "g", 2000),
    ("Mint Leaves", "g", 1000),
]

# (name, price, gst_percentage, stock_item_name, qty_per_serving)
ALCOHOL_ITEMS = [
    ("Draft Beer (pint)", 220, 18, "Draft Beer Keg", 500),
    ("Bottled Beer", 250, 18, "Beer Bottle", 1),
    ("House Wine (glass)", 350, 18, "Wine Bottle", 150),
    ("Whiskey (30ml)", 300, 18, "Whiskey Bottle", 30),
    ("Vodka (30ml)", 280, 18, "Vodka Bottle", 30),
    ("Rum (30ml)", 260, 18, "Rum Bottle", 30),
    ("Classic Mojito", 380, 18, "Rum Bottle", 60),
    ("Margarita", 400, 18, "Tequila Bottle", 60),
    ("Old Fashioned", 450, 18, "Whiskey Bottle", 30),
]

# (name, unit, starting_stock)
BAR_STOCK = [
    ("Draft Beer Keg", "ml", 30000),
    ("Beer Bottle", "pcs", 120),
    ("Wine Bottle", "ml", 15000),
    ("Whiskey Bottle", "ml", 15000),
    ("Vodka Bottle", "ml", 7500),
    ("Rum Bottle", "ml", 11250),
    ("Tequila Bottle", "ml", 7500),
]


class Command(BaseCommand):
    help = "Simulate a full pub night (tabs, alcohol, spillage, unhappy paths) end to end."

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=float, default=5,
                             help="Run duration in real hours. Use 0.1 (~6 min) as a dry run first.")
        parser.add_argument("--tables", type=int, default=10,
                             help="Number of pub tables (= worker threads).")
        parser.add_argument("--checkpoint-minutes", type=float, default=15,
                             help="Target checkpoint interval (auto-shrinks for short --hours).")
        parser.add_argument("--keep", action="store_true",
                             help="Keep the PubTest tenant + data instead of cleaning up.")
        parser.add_argument("--log-dir", default="loadtest_logs",
                             help="Directory for the timestamped run log.")

    # ------------------------------------------------------------------ setup
    def _setup(self, n_tables):
        tenant, _ = Tenant.objects.get_or_create(
            slug=PUBTEST_SLUG, defaults={"name": "PubTest"}
        )
        # Dummy but validator-passing GSTIN/FSSAI so this reads as a real,
        # fully onboarded restaurant, not a bare-minimum test fixture --
        # gst_no is literally the example value from the model's own help
        # text, guaranteed to pass gstin_validator.
        outlet, _ = Outlet.objects.get_or_create(
            tenant=tenant, name="The Rusty Anchor Pub",
            defaults={
                "address": "12 Brew Street, Indiranagar, Bengaluru, Karnataka 560038",
                "gst_no": "29ABCDE1234F1Z5",
                "fssai_no": "12345678901234",
                "phone": "+919876543210",
                "email": "contact@pubtest.example",
            },
        )

        owner, created = User.objects.get_or_create(
            username="pubtest_owner",
            defaults={"role": "owner", "tenant": tenant, "outlet": outlet},
        )
        if created:
            owner.set_password("pubtest")
            owner.tenant, owner.outlet, owner.role = tenant, outlet, "owner"
            owner.save()

        PaymentConfig.objects.get_or_create(
            tenant=tenant, outlet=outlet,
            defaults={"cash_enabled": True, "upi_enabled": True, "card_enabled": True},
        )
        CashSession.objects.get_or_create(
            tenant=tenant, outlet=outlet, status="open",
            defaults={"date": timezone.now().date(), "opened_by": owner},
        )

        category, _ = MenuCategory.objects.get_or_create(
            tenant=tenant, outlet=outlet, name="Pub Menu"
        )

        supplier, _ = Supplier.objects.get_or_create(
            tenant=tenant, outlet=outlet, name="Dummy Pub Supply Co.",
            defaults={"contact_person": "Test Supplier", "phone": "+911234567890",
                      "email": "supplier@pubtest.example", "is_active": True},
        )

        # One InventoryItem per real ingredient/bar stock line, bar and
        # kitchen alike -- both categories get the same reorder wiring so
        # low-stock food ingredients trigger a real PO exactly like alcohol.
        stock_by_name = {}
        for name, unit, qty, cat in (
            [(n, u, q, "Bar") for n, u, q in BAR_STOCK]
            + [(n, u, q, "Kitchen") for n, u, q in FOOD_STOCK]
        ):
            threshold = Decimal(str(qty)) * Decimal("0.1")
            stock_item, _ = InventoryItem.objects.get_or_create(
                tenant=tenant, outlet=outlet, name=name,
                defaults={
                    "category": cat, "unit": unit, "stock": Decimal(str(qty)),
                    "low_stock_threshold": threshold,
                    # trigger_reorder() needs all three of these set to fire --
                    # same eligibility rule generate_purchase_orders() checks.
                    "preferred_supplier": supplier,
                    "reorder_quantity": Decimal(str(qty)) * Decimal("0.5"),
                    "cost_price": Decimal("50.00"),
                },
            )
            stock_by_name[name] = stock_item

        food_items, alcohol_items = [], []
        for name, price, gst, stock_name, qty_per in FOOD_ITEMS:
            item, _ = MenuItem.objects.get_or_create(
                tenant=tenant, outlet=outlet, name=name,
                defaults={"category": category, "price": price, "gst_percentage": gst},
            )
            food_items.append((item, stock_by_name[stock_name], Decimal(str(qty_per))))

        for name, price, gst, stock_name, qty_per in ALCOHOL_ITEMS:
            item, _ = MenuItem.objects.get_or_create(
                tenant=tenant, outlet=outlet, name=name,
                defaults={"category": category, "price": price, "gst_percentage": gst},
            )
            alcohol_items.append((item, stock_by_name[stock_name], Decimal(str(qty_per))))

        existing = list(Table.objects.filter(tenant=tenant, outlet=outlet))
        for i in range(len(existing), n_tables):
            existing.append(Table.objects.create(
                tenant=tenant, outlet=outlet, name=f"Pub{i + 1}",
                qr_token=uuid.uuid4(),
            ))
        tables = existing[:n_tables]
        for t in tables:
            if not t.qr_token:
                t.qr_token = uuid.uuid4()
                t.save(update_fields=["qr_token"])

        return tenant, outlet, owner, tables, food_items, alcohol_items, list(stock_by_name.values())

    # -------------------------------------------------------------- helpers
    def _pick_round(self, food_items, alcohol_items):
        """food_items/alcohol_items are both [(menu_item, stock_item, qty_per), ...].
        Returns [(menu_item, qty, stock_item, qty_per_serving), ...] -- every
        item now consumes a real ingredient, food and drink alike."""
        n = random.randint(1, 4)
        picks = []
        for _ in range(n):
            pool = alcohol_items if (random.random() < 0.55 and alcohol_items) else food_items
            item, stock_item, qty_per = random.choice(pool)
            qty = random.randint(1, 2)
            picks.append((item, qty, stock_item, qty_per))
        return picks

    def _consume_stock(self, stock_item, qty):
        """Best-effort deduction mirroring InventoryItem's own F()-expression
        pattern (record_wastage/reduce_stock) -- clamped at 0 rather than
        going negative, and returns False (not raised) when stock can't
        cover the pour so callers can log it as a real "ran dry" event."""
        updated = InventoryItem.objects.filter(
            id=stock_item.id, stock__gte=qty
        ).update(stock=F("stock") - qty)
        return updated == 1

    def _maybe_reorder(self, stock_item, bump):
        """After any deduction (a pour or a spillage), check the real
        low_stock_threshold and fire the same trigger_reorder() the Auto-PO
        button uses -- same one-draft-PO-per-supplier consolidation logic,
        not reimplemented here. get_or_create inside trigger_reorder means
        calling it repeatedly while already-low is safe, not duplicate POs."""
        stock_item.refresh_from_db()
        if stock_item.stock > stock_item.low_stock_threshold:
            return
        try:
            stock_item.trigger_reorder()
            bump("reorders_triggered")
        except Exception as exc:  # noqa: BLE001
            bump(f"error_{type(exc).__name__}")

    def _request(self, method, path, body, user, ip):
        rf = RequestFactory()
        req = rf.post(path, data=_json.dumps(body, default=str),
                       content_type="application/json", REMOTE_ADDR=ip)
        if user is not None:
            req.user = user
        return req

    # -------------------------------------------------------------- tab loop
    def _table_worker(self, table, owner, food_items, alcohol_items, stop_event,
                       round_min_s, round_max_s, tab_min_s, tab_max_s, counters, lock, log):
        ip = f"10.{random.randint(0, 254)}.{random.randint(0, 254)}.{random.randint(1, 254)}"

        def bump(key):
            with lock:
                counters[key] = counters.get(key, 0) + 1

        while not stop_event.is_set():
            is_qr = random.random() < 0.35
            channel = "qr" if is_qr else "staff"
            bump(f"channel_{channel}")

            try:
                first_round = self._pick_round(food_items, alcohol_items)
                cart = [{"id": mi.id, "quantity": qty, "modifiers": []} for mi, qty, _, _ in first_round]

                if is_qr:
                    req = self._request("POST", "/orders/create/",
                                         {"cart": cart, "table_token": str(table.qr_token)}, None, ip)
                    resp = create_order(req)
                    if resp.status_code != 200:
                        bump("open_tab_error")
                        connection.close()
                        continue
                    order_id = _json.loads(resp.content)["order_id"]
                    order = Order.objects.get(id=order_id)
                else:
                    order = get_or_create_open_order(owner, table)
                    add_items_to_order(owner, order, cart)
                    create_kot(owner, order)
                bump("tabs_opened")
                for mi, qty, stock_item, qty_per in first_round:
                    if stock_item:
                        if not self._consume_stock(stock_item, qty_per * qty):
                            bump("stock_ran_dry")
                        self._maybe_reorder(stock_item, bump)
                bump("rounds_served")
            except Exception as exc:  # noqa: BLE001
                bump(f"error_{type(exc).__name__}")
                connection.close()
                continue

            tab_deadline = time.time() + random.uniform(tab_min_s, tab_max_s)
            cancelled_this_tab = False
            while time.time() < tab_deadline and not stop_event.is_set():
                time.sleep(random.uniform(round_min_s, round_max_s))
                if time.time() >= tab_deadline:
                    break
                try:
                    order.refresh_from_db()
                    if order.status in ("paid", "closed", "cancelled"):
                        break
                    round_items = self._pick_round(food_items, alcohol_items)
                    cart = [{"id": mi.id, "quantity": qty, "modifiers": []} for mi, qty, _, _ in round_items]
                    if is_qr:
                        req = self._request(
                            "POST", "/orders/create/",
                            {"cart": cart, "table_token": str(table.qr_token), "order_id": order.id},
                            None, ip,
                        )
                        resp = create_order(req)
                        if resp.status_code != 200:
                            bump("add_round_error")
                            continue
                    else:
                        add_items_to_order(owner, order, cart)
                        create_kot(owner, order)
                    for mi, qty, stock_item, qty_per in round_items:
                        if stock_item:
                            if not self._consume_stock(stock_item, qty_per * qty):
                                bump("stock_ran_dry")
                            self._maybe_reorder(stock_item, bump)
                    bump("rounds_served")

                    # occasional mid-order cancel -- once per tab at most
                    if not cancelled_this_tab and random.random() < 0.08:
                        item_row = order.items.order_by("-id").first()
                        if item_row:
                            before = item_row.menu_item.name
                            stock_before = None
                            match = next((s for m, s, q in
                                          [(mi, si, qp) for mi, _, si, qp in round_items] if m.name == before), None)
                            void_order_item(owner, item_row.id, "Simulated pub-night cancel")
                            cancelled_this_tab = True
                            bump("items_cancelled")

                    # occasional item goes unavailable mid-run
                    if random.random() < 0.02 and alcohol_items:
                        mi, stock_item, _ = random.choice(alcohol_items)
                        if stock_item.stock <= stock_item.low_stock_threshold:
                            mi.is_available = False
                            mi.save(update_fields=["is_available"])
                            bump("items_marked_unavailable")
                except Exception as exc:  # noqa: BLE001
                    bump(f"error_{type(exc).__name__}")
                    connection.close()

            # ---- close the tab ----
            try:
                order.refresh_from_db()
                if order.status not in ("paid", "closed", "cancelled"):
                    self._close_tab(order, owner, ip, bump)
            except Exception as exc:  # noqa: BLE001
                bump(f"error_{type(exc).__name__}")

            # ---- stale re-order probe: try the now-closed order_id/QR again ----
            if is_qr and random.random() < 0.15:
                try:
                    req = self._request(
                        "POST", "/orders/create/",
                        {"cart": [{"id": food_items[0][0].id, "quantity": 1, "modifiers": []}],
                         "table_token": str(table.qr_token), "order_id": order.id},
                        None, ip,
                    )
                    resp = create_order(req)
                    if resp.status_code == 409:
                        bump("stale_qr_blocked_correctly")
                    else:
                        bump("stale_qr_NOT_blocked")
                except Exception as exc:  # noqa: BLE001
                    bump(f"error_{type(exc).__name__}")

            connection.close()

        connection.close()

    def _close_tab(self, order, owner, ip, bump):
        roll = random.random()
        if roll < 0.06:
            # complimentary -- real ₹0 branch, not a skipped payment
            # pay_order's complimentary branch checks grand_total == 0, not
            # the amount sent -- a real ₹0 order, not just a ₹0 tender on a
            # normal one (correctly rejected otherwise). Comp it for real
            # first, same as a manager applying a 100% discount would.
            order.refresh_from_db()
            order.apply_discount("percentage", Decimal("100"))
            req = self._request("POST", f"/orders/{order.id}/pay/",
                                 {"method": "cash", "amount": "0"}, owner, ip)
            resp = pay_order(req, order.id)
            bump("closed_complimentary" if resp.status_code == 200 else "close_error_complimentary")
        elif roll < 0.14:
            req = self._request("POST", f"/orders/{order.id}/split-pay/",
                                 {"people": random.randint(2, 4), "method": "cash"}, owner, ip)
            resp = split_pay(req, order.id)
            bump("closed_split" if resp.status_code == 200 else "close_error_split")
        elif roll < 0.20:
            req = self._request("POST", f"/orders/{order.id}/log-bypass/", {}, owner, ip)
            resp = log_bypass(req, order.id)
            bump("closed_manager_bypass" if resp.status_code == 200 else "close_error_bypass")
        else:
            order.refresh_from_db()
            method = random.choice(["cash", "upi", "card"])
            req = self._request("POST", f"/orders/{order.id}/pay/",
                                 {"method": method, "amount": str(order.grand_total)}, owner, ip)
            resp = pay_order(req, order.id)
            bump("closed_normal" if resp.status_code == 200 else "close_error_normal")

    # ---------------------------------------------------------- spillage loop
    def _spillage_loop(self, stock_items, tick_seconds, stop_event, counters, lock, log):
        while not stop_event.wait(tick_seconds):
            item = random.choice(stock_items)
            item.refresh_from_db()
            qty = min(item.stock, Decimal(str(round(random.uniform(20, 150), 2))))
            with lock:
                if qty <= 0:
                    counters["spillage_skipped_dry"] = counters.get("spillage_skipped_dry", 0) + 1
                    continue
            def bump(key):
                with lock:
                    counters[key] = counters.get(key, 0) + 1

            try:
                item.record_wastage(qty, reference="Simulated spillage -- pub night test")
                bump("spillage_events")
                with lock:
                    counters["spillage_qty_total"] = counters.get("spillage_qty_total", 0) + float(qty)
                self._maybe_reorder(item, bump)
            except Exception as exc:  # noqa: BLE001
                bump(f"error_{type(exc).__name__}")
            connection.close()

    # ------------------------------------------------------------------- main
    def handle(self, *args, **opts):
        log = RunLogger(self.stdout.write, opts["log_dir"], "pub_night_test")

        if "sqlite" in connection.vendor:
            log.write(self.style.WARNING(
                "DB backend is SQLite -- concurrency numbers won't be representative. "
                "Point DB_* env vars at Postgres for a real result.\n"
            ))

        hours = opts["hours"]
        n_tables = opts["tables"]
        total_seconds = hours * 3600
        round_min_s = total_seconds / 40
        round_max_s = total_seconds / 16
        tab_min_s = total_seconds * 0.1875
        tab_max_s = total_seconds * 0.5
        spillage_tick_s = max(10, total_seconds / 8)
        checkpoint_s = max(10, min(opts["checkpoint_minutes"] * 60, total_seconds / 6))

        log.write(self.style.MIGRATE_HEADING(
            f"\nPub Night Simulation -- {hours}h run, {n_tables} tables "
            f"(round every {round_min_s:.0f}-{round_max_s:.0f}s, "
            f"tab open {tab_min_s / 60:.1f}-{tab_max_s / 60:.1f}min, "
            f"spillage every ~{spillage_tick_s:.0f}s, checkpoint every {checkpoint_s:.0f}s)"
        ))

        tenant, outlet, owner, tables, food_items, alcohol_items, stock_items = self._setup(n_tables)
        log.write(f"Setup complete: {len(tables)} tables, {len(food_items)} food items, "
                  f"{len(alcohol_items)} alcohol items, {len(stock_items)} bar stock lines.")

        stop_event = threading.Event()
        counters, lock = {}, threading.Lock()

        threads = [
            threading.Thread(target=self._table_worker,
                              args=(t, owner, food_items, alcohol_items, stop_event,
                                    round_min_s, round_max_s, tab_min_s, tab_max_s,
                                    counters, lock, log),
                              daemon=True)
            for t in tables
        ]
        spill_thread = threading.Thread(target=self._spillage_loop,
                                         args=(stock_items, spillage_tick_s, stop_event, counters, lock, log),
                                         daemon=True)

        wall_start = time.perf_counter()
        for th in threads:
            th.start()
        spill_thread.start()

        end_at = wall_start + total_seconds
        mem_series = []
        while True:
            now = time.perf_counter()
            remaining = end_at - now
            if remaining <= 0:
                break
            sampler = ResourceSampler().start()
            time.sleep(min(checkpoint_s, remaining))
            res = sampler.stop()
            elapsed = time.perf_counter() - wall_start
            db = db_snapshot()
            db_bit = (f"  db_conns={db['active_connections']} waiting_locks={db['waiting_locks']}"
                      if db else "")
            with lock:
                snap = dict(counters)
            log.write(
                f"  t={elapsed / 60:.1f}min  tabs={snap.get('tabs_opened', 0)} "
                f"rounds={snap.get('rounds_served', 0)} "
                f"closed={sum(v for k, v in snap.items() if k.startswith('closed_'))} "
                f"spillage={snap.get('spillage_events', 0)} "
                f"errors={sum(v for k, v in snap.items() if k.startswith('error_'))}  "
                f"{format_resource_line('mem/cpu', res)}{db_bit}"
            )
            if res.get("available") and res.get("samples", 0):
                mem_series.append((elapsed, res["mem_mb_mean"]))

        stop_event.set()
        for th in threads:
            th.join(timeout=15)
        spill_thread.join(timeout=15)
        wall = time.perf_counter() - wall_start

        # ---------------- final verdict ----------------
        log.write(self.style.MIGRATE_HEADING("\nFinal Verdict"))
        log.write(f"  wall time            : {wall / 60:.1f} min")
        log.write(f"  tabs opened          : {counters.get('tabs_opened', 0)}  "
                  f"(staff={counters.get('channel_staff', 0)}  qr={counters.get('channel_qr', 0)})")
        log.write(f"  rounds served        : {counters.get('rounds_served', 0)}")
        log.write(f"  closed normal        : {counters.get('closed_normal', 0)}")
        log.write(f"  closed complimentary : {counters.get('closed_complimentary', 0)}")
        log.write(f"  closed split         : {counters.get('closed_split', 0)}")
        log.write(f"  closed mgr bypass    : {counters.get('closed_manager_bypass', 0)}")
        log.write(f"  items cancelled      : {counters.get('items_cancelled', 0)}")
        log.write(f"  items marked unavail.: {counters.get('items_marked_unavailable', 0)}")
        log.write(f"  stock ran dry events : {counters.get('stock_ran_dry', 0)}")
        log.write(f"  reorders triggered   : {counters.get('reorders_triggered', 0)} (draft POs via trigger_reorder())")
        log.write(f"  spillage events      : {counters.get('spillage_events', 0)}  "
                  f"(total qty ~{counters.get('spillage_qty_total', 0):.1f})")
        log.write(f"  stale QR blocked OK  : {counters.get('stale_qr_blocked_correctly', 0)}")
        stale_bad = counters.get("stale_qr_NOT_blocked", 0)
        if stale_bad:
            log.write(self.style.ERROR(f"  stale QR NOT blocked : {stale_bad}  <-- real bug, investigate"))

        error_keys = {k: v for k, v in counters.items() if k.startswith("error_") or "error" in k}
        if error_keys:
            log.write(self.style.WARNING("  errors / rejections:"))
            for k, v in sorted(error_keys.items(), key=lambda x: -x[1]):
                log.write(f"    {k}: {v}")
        else:
            log.write(self.style.SUCCESS("  no errors logged."))

        verdict = "PASS"
        if stale_bad:
            verdict = "FAIL"
        elif len(mem_series) >= 4:
            half = len(mem_series) // 2
            first_half = statistics.mean(m for _, m in mem_series[:half])
            second_half = statistics.mean(m for _, m in mem_series[half:])
            growth_pct = ((second_half - first_half) / first_half * 100) if first_half else 0
            still_climbing = mem_series[-1][1] > mem_series[-2][1]
            log.write(f"  memory trend         : {first_half:.0f}MB -> {second_half:.0f}MB ({growth_pct:+.0f}%)")
            if growth_pct > 20 and still_climbing:
                verdict = "WATCH"
                log.write(self.style.WARNING(
                    "  WATCH: memory grew >20% and was still climbing -- rerun longer to confirm a real leak."
                ))
        else:
            log.write("  (too few checkpoints to judge a memory trend -- this is expected on a short dry run)")

        style = {"PASS": self.style.SUCCESS, "WATCH": self.style.WARNING, "FAIL": self.style.ERROR}[verdict]
        log.write(style(f"\n  VERDICT: {verdict}"))

        if opts["keep"]:
            log.write(self.style.WARNING(f"\n--keep set: leaving PubTest tenant '{tenant.slug}' in place."))
        else:
            log.write("\nCleaning up PubTest tenant...")
            cleanup_tenant(tenant)
            log.write(self.style.SUCCESS("Cleanup complete."))

        log.write(f"\nFull log saved to: {log.path}")
        log.close()
