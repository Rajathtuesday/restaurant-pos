"""
Parcel Charge — Complete Test Suite
====================================

HOW THE SYSTEM WORKS
--------------------
Two data points live on the server:

  Outlet.parcel_charge_amount   — unit price per item (e.g. ₹5). Set once in settings.
  Order.parcel_surcharge        — actual charge on a specific order (e.g. ₹25 for 5 items).
                                  Starts at 0. Written only by toggle_parcel.

TOGGLE LOGIC (order_actions.toggle_parcel)
------------------------------------------
One HTTP POST = one flip:

  if order.parcel_surcharge > 0  →  set to 0               (OFF)
  else:
    if any MenuItem has its own parcel_charge  →  sum those overrides × qty
    elif outlet.parcel_charge_per_item         →  outlet_charge × total_active_qty
    else                                       →  outlet_charge (flat, once)

  voided OrderItems are excluded from all qty calculations.
  Calls order.recalculate_totals() after every change.

ALLOWED ORDER STATUSES: "open", "billing". Closed/cancelled → 404.

CLIENT-SIDE STATE (billing.html)
---------------------------------
  _parcelOn             — user intent (do they want parcel?)
  _parcelAppliedToOrder — order_id that parcel is actually applied to on the server
  _parcelAmount         — read-only unit price from outlet settings

BUG THAT WAS FIXED
-------------------
The old sendToKitchen fired toggle-parcel every dispatch when _parcelOn=true.
For dine-in, create-order returns the SAME order_id on subsequent dispatches
(same open table order). The second toggle call found parcel_surcharge > 0
and turned it OFF. Bill showed ₹0 parcel.

THREE GAPS FIXED
-----------------
Gap 1 — kitchen send fails after parcel applied:
    Old: _parcelAppliedToOrder was set in the kitchen-send success block.
         If kitchen send failed, the next dispatch re-toggled parcel OFF.
    Fix: _parcelAppliedToOrder is now set inside the parcel fetch's own .then(),
         before the kitchen send. A kitchen-send failure leaves parcel ON.

Gap 2 — session resume (page refresh):
    Old: running_order_items returned no parcel info. After refresh _parcelOn=false,
         _parcelAppliedToOrder=null. If user toggled ON and dispatched, the client
         fired toggle-parcel which found parcel_surcharge>0 and turned it OFF.
    Fix: running_order_items now returns parcel_on + parcel_amount.
         loadRunningOrder() syncs _parcelOn and _parcelAppliedToOrder from those values.

Gap 3 — QSR path:
    Old: submitQSROrder called toggle-parcel but never updated _parcelAppliedToOrder.
    Fix: _parcelAppliedToOrder = qsrPendingOrderId set right after the await.

Run: python manage.py test orders.tests.test_parcel_dispatch --keepdb
"""
from decimal import Decimal

from django.test import Client, TestCase

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from setup.models import PaymentConfig
from tenants.models import Outlet, Tenant


# ── Shared fixture ─────────────────────────────────────────────────────────────

class ParcelBase(TestCase):
    """Outlet with ₹5 per-item parcel charge. Three menu items."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Parcel Dispatch Test")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Counter",
            parcel_charge_amount=Decimal("5"),
            parcel_charge_per_item=True,
        )
        PaymentConfig.objects.create(
            tenant=self.tenant, outlet=self.outlet, cash_enabled=True,
        )
        self.owner = User.objects.create_user(
            username="parcel_owner", password="testpass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Food"
        )
        self.idli = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.category, name="Idli",
            price=40, gst_percentage=5, parcel_charge=Decimal("0"),
        )
        self.dosa = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.category, name="Dosa",
            price=60, gst_percentage=5, parcel_charge=Decimal("0"),
        )
        self.juice = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=self.category, name="Juice",
            price=50, gst_percentage=0, parcel_charge=Decimal("0"),
        )
        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="T1"
        )
        self.client = Client()
        self.client.login(username="parcel_owner", password="testpass")

    def _make_order(self, items=None, table=None, status="open"):
        """Create an order with given [(menu_item, qty), ...] pairs."""
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            created_by=self.owner, source="counter",
            table=table, status=status,
        )
        if items is None:
            items = [(self.idli, 2)]
        for menu_item, qty in items:
            OrderItem.objects.create(
                order=order, menu_item=menu_item,
                quantity=qty, price=menu_item.price,
                gst_percentage=menu_item.gst_percentage,
                total_price=menu_item.price * qty,
            )
        order.recalculate_totals()
        return order

    def _toggle(self, order_id):
        return self.client.post(f"/toggle-parcel/{order_id}/")

    def _running(self, *, order_id=None, table_id=None):
        if order_id:
            return self.client.get(f"/running-order-items/?order={order_id}")
        return self.client.get(f"/running-order-items/?table={table_id}")


# ── 1. Basic toggle behaviour ──────────────────────────────────────────────────

class ToggleBasicTests(ParcelBase):
    """Core flip-flop contract of toggle_parcel."""

    def test_single_toggle_turns_parcel_on(self):
        order = self._make_order([(self.idli, 2)])  # 2 items
        resp = self._toggle(order.id)
        self.assertEqual(resp.status_code, 200)
        d = resp.json()
        self.assertTrue(d["parcel_on"])
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("10"))  # 2 × ₹5

    def test_double_toggle_turns_parcel_off(self):
        """This is the server behaviour that the JS fix prevents triggering accidentally."""
        order = self._make_order([(self.idli, 2)])
        self._toggle(order.id)          # ON
        resp = self._toggle(order.id)   # OFF
        d = resp.json()
        self.assertFalse(d["parcel_on"])
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("0"))

    def test_parcel_stays_on_without_second_toggle(self):
        """
        Proves the fix: once parcel is applied with one toggle, NOT calling toggle
        again leaves parcel_surcharge intact on the order.
        The JS fix ensures the second dispatch never calls toggle.
        """
        order = self._make_order([(self.idli, 3)])
        self._toggle(order.id)
        order.refresh_from_db()
        self.assertEqual(order.parcel_surcharge, Decimal("15"))  # 3 × ₹5
        self.assertEqual(order.grand_total, order.subtotal + order.gst_total + Decimal("15"))

    def test_toggle_response_includes_grand_total(self):
        order = self._make_order([(self.idli, 1)])
        d = self._toggle(order.id).json()
        self.assertIn("grand_total", d)
        self.assertGreater(d["grand_total"], 0)

    def test_toggle_on_open_status_works(self):
        order = self._make_order(status="open")
        resp = self._toggle(order.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["parcel_on"])

    def test_toggle_on_billing_status_works(self):
        order = self._make_order(status="billing")
        resp = self._toggle(order.id)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["parcel_on"])

    def test_toggle_on_closed_order_returns_404(self):
        order = self._make_order()
        order.status = "closed"
        order.save(update_fields=["status"])
        resp = self._toggle(order.id)
        self.assertEqual(resp.status_code, 404)

    def test_toggle_on_cancelled_order_returns_404(self):
        order = self._make_order()
        order.status = "cancelled"
        order.save(update_fields=["status"])
        resp = self._toggle(order.id)
        self.assertEqual(resp.status_code, 404)

    def test_toggle_on_nonexistent_order_returns_404(self):
        resp = self._toggle(999999)
        self.assertEqual(resp.status_code, 404)


# ── 2. Charge calculation modes ────────────────────────────────────────────────

class ToggleCalculationTests(ParcelBase):
    """All three branches of the surcharge calculation."""

    def test_per_item_mode_multiplies_by_quantity(self):
        """outlet.parcel_charge_per_item=True, no item-level overrides → charge × qty."""
        order = self._make_order([(self.idli, 4)])
        d = self._toggle(order.id).json()
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("20"))  # 4 × ₹5

    def test_flat_mode_ignores_quantity(self):
        """outlet.parcel_charge_per_item=False → flat charge regardless of qty."""
        self.outlet.parcel_charge_per_item = False
        self.outlet.save(update_fields=["parcel_charge_per_item"])
        order = self._make_order([(self.idli, 5)])
        d = self._toggle(order.id).json()
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("5"))  # flat ₹5

    def test_per_item_menu_override_takes_priority(self):
        """
        If a MenuItem has parcel_charge > 0, that overrides the outlet default.
        Sum = item.parcel_charge × item.quantity for ALL overriding items.
        The outlet.parcel_charge_amount is NOT used.
        """
        self.idli.parcel_charge = Decimal("8")
        self.idli.save(update_fields=["parcel_charge"])
        order = self._make_order([(self.idli, 3)])  # 3 × ₹8 = ₹24
        d = self._toggle(order.id).json()
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("24"))

    def test_mixed_items_only_override_items_contribute(self):
        """
        Some items have parcel_charge override, some don't.
        Only items WITH override contribute; items without use nothing
        (the outlet default is not mixed in).
        """
        self.idli.parcel_charge = Decimal("8")
        self.idli.save(update_fields=["parcel_charge"])
        # dosa has parcel_charge=0 — should not contribute
        order = self._make_order([(self.idli, 2), (self.dosa, 3)])
        d = self._toggle(order.id).json()
        # Expected: 2 × ₹8 = ₹16 (dosa contributes ₹0)
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("16"))

    def test_no_override_and_per_item_mode_uses_all_items(self):
        """
        No item-level overrides. per_item=True.
        outlet charge × (idli_qty + dosa_qty).
        """
        order = self._make_order([(self.idli, 2), (self.dosa, 3)])
        d = self._toggle(order.id).json()
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("25"))  # 5 × ₹5

    def test_zero_outlet_charge_leaves_parcel_off(self):
        """Outlet with parcel_charge_amount=0 → nothing to charge → parcel_on=False."""
        self.outlet.parcel_charge_amount = Decimal("0")
        self.outlet.save(update_fields=["parcel_charge_amount"])
        order = self._make_order([(self.idli, 3)])
        d = self._toggle(order.id).json()
        self.assertFalse(d["parcel_on"])
        self.assertEqual(d["parcel_amount"], 0.0)

    def test_voided_items_excluded_from_per_item_calculation(self):
        """Voided OrderItems must not count toward the qty calculation."""
        order = self._make_order([(self.idli, 2), (self.dosa, 3)])
        # Void one of the dosa items
        voided = order.items.filter(menu_item=self.dosa).first()
        voided.status = "voided"
        voided.save(update_fields=["status"])

        d = self._toggle(order.id).json()
        # Only the idli (2 qty) should count; dosa is voided → 2 × ₹5 = ₹10
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("10"))

    def test_all_items_voided_results_in_zero_parcel(self):
        """All items voided → no active qty → parcel_surcharge=0."""
        order = self._make_order([(self.idli, 2)])
        order.items.update(status="voided")
        d = self._toggle(order.id).json()
        self.assertFalse(d["parcel_on"])
        self.assertEqual(d["parcel_amount"], 0.0)


# ── 3. Grand total integration ─────────────────────────────────────────────────

class ToggleTotalIntegrationTests(ParcelBase):
    """recalculate_totals must include parcel_surcharge in grand_total."""

    def test_grand_total_increases_by_parcel_amount(self):
        order = self._make_order([(self.idli, 2)])
        before = order.grand_total
        self._toggle(order.id)
        order.refresh_from_db()
        self.assertEqual(order.grand_total, before + Decimal("10"))

    def test_grand_total_restores_after_toggle_off(self):
        order = self._make_order([(self.idli, 2)])
        original = order.grand_total
        self._toggle(order.id)   # ON
        self._toggle(order.id)   # OFF
        order.refresh_from_db()
        self.assertEqual(order.grand_total, original)

    def test_parcel_preserved_after_recalculate_without_retoggle(self):
        """
        recalculate_totals preserves parcel_surcharge.
        This mirrors what happens when the kitchen sends more items —
        the JS fix skips toggle, totals are recalculated, parcel stays.
        """
        order = self._make_order([(self.idli, 2)])
        self._toggle(order.id)  # apply ₹10 parcel
        order.refresh_from_db()
        surcharge_before = order.parcel_surcharge

        # Simulate more items added (second dispatch — no re-toggle)
        OrderItem.objects.create(
            order=order, menu_item=self.dosa,
            quantity=1, price=self.dosa.price,
            gst_percentage=self.dosa.gst_percentage,
            total_price=self.dosa.price,
        )
        order.recalculate_totals()
        order.refresh_from_db()

        self.assertEqual(order.parcel_surcharge, surcharge_before)
        self.assertEqual(order.grand_total,
                         order.subtotal + order.gst_total + surcharge_before)


# ── 4. Gap 2 — running_order_items returns parcel state ────────────────────────

class RunningOrderParcelStateTests(ParcelBase):
    """
    Gap 2 fix: running_order_items now includes parcel_on and parcel_amount
    so the client can sync state on page load / table switch / session resume.
    """

    def test_response_always_has_parcel_keys(self):
        """Keys present even when parcel is off."""
        order = self._make_order()
        d = self._running(order_id=order.id).json()
        self.assertIn("parcel_on", d)
        self.assertIn("parcel_amount", d)

    def test_parcel_off_by_default(self):
        order = self._make_order()
        d = self._running(order_id=order.id).json()
        self.assertFalse(d["parcel_on"])
        self.assertEqual(d["parcel_amount"], 0.0)

    def test_parcel_on_after_toggle(self):
        order = self._make_order([(self.idli, 2)])
        self._toggle(order.id)
        d = self._running(order_id=order.id).json()
        self.assertTrue(d["parcel_on"])
        self.assertEqual(d["parcel_amount"], 10.0)  # 2 × ₹5

    def test_parcel_off_after_double_toggle(self):
        order = self._make_order([(self.idli, 2)])
        self._toggle(order.id)
        self._toggle(order.id)
        d = self._running(order_id=order.id).json()
        self.assertFalse(d["parcel_on"])
        self.assertEqual(d["parcel_amount"], 0.0)

    def test_lookup_by_table_id(self):
        """Can look up parcel state via table_id, not just order_id."""
        order = self._make_order([(self.idli, 2)], table=self.table)
        self._toggle(order.id)
        d = self._running(table_id=self.table.id).json()
        self.assertEqual(d["order_id"], order.id)
        self.assertTrue(d["parcel_on"])
        self.assertEqual(d["parcel_amount"], 10.0)

    def test_no_order_for_table_returns_null_without_crash(self):
        """
        Hole 1 fix: when no open order exists for a table, order_id=None.
        The client's loadRunningOrder() checks `if (d.order_id)` — the else branch
        now resets _parcelOn=false and _parcelAppliedToOrder=null so parcel state
        from a PREVIOUS table does not bleed into a fresh table selection.
        """
        d = self._running(table_id=self.table.id).json()
        self.assertIsNone(d["order_id"])

    def test_paid_order_no_longer_returned_as_open(self):
        """
        Hole 1 scenario: after an order is paid/closed, the same table lookup
        returns order_id=None. This is the server signal that triggers the client
        to reset _parcelOn=false so the parcel button is not still active for
        the next customer at that table.
        """
        order = self._make_order([(self.idli, 2)], table=self.table)
        self._toggle(order.id)  # apply parcel
        # Close the order (simulate payment complete)
        order.status = "closed"
        order.save(update_fields=["status"])

        d = self._running(table_id=self.table.id).json()
        self.assertIsNone(d["order_id"])  # no open order → client resets parcel state

    def test_session_resume_restores_parcel_state(self):
        """
        Simulates a page refresh scenario:
        - Parcel was applied in a previous browser session (set directly on DB).
        - Client calls running_order_items to re-sync.
        - Response must carry parcel_on=True so client sets _parcelAppliedToOrder
          correctly and does NOT fire toggle again.
        """
        order = self._make_order([(self.idli, 3)], table=self.table)
        order.parcel_surcharge = Decimal("15")
        order.save(update_fields=["parcel_surcharge"])
        order.recalculate_totals()

        d = self._running(order_id=order.id).json()
        self.assertTrue(d["parcel_on"])
        self.assertEqual(d["parcel_amount"], 15.0)
        self.assertEqual(d["order_id"], order.id)


# ── 5. Gap 1 — parcel state recorded before kitchen send ──────────────────────

class ParcelAppliedBeforeKitchenSendTests(ParcelBase):
    """
    Gap 1 fix: _parcelAppliedToOrder is set inside the parcel fetch's own .then(),
    not in the outer kitchen-send success block.

    Python tests can only verify the server state. The JS guarantee is:
    if the parcel fetch succeeds (server has parcel ON), the client records it
    even if the subsequent kitchen-send call fails.

    These tests prove the server leaves parcel ON after a single toggle — so
    a client that correctly records _parcelAppliedToOrder after the parcel
    fetch will NOT re-fire toggle on retry.
    """

    def test_parcel_on_server_after_single_toggle(self):
        """Server has parcel ON after one toggle — retry must not toggle again."""
        order = self._make_order([(self.idli, 2)])
        self._toggle(order.id)
        order.refresh_from_db()
        self.assertTrue(order.parcel_surcharge > 0)

    def test_second_toggle_would_turn_off_proving_client_must_not_retry(self):
        """
        This test documents the server behaviour that makes Gap 1 dangerous:
        if the client fires toggle twice, parcel goes OFF.
        The fix ensures the client only fires once per order.
        """
        order = self._make_order([(self.idli, 2)])
        self._toggle(order.id)   # parcel ON — what the client does on dispatch
        self._toggle(order.id)   # parcel OFF — what would happen on a naive retry
        order.refresh_from_db()
        self.assertEqual(order.parcel_surcharge, Decimal("0"))

    def test_parcel_persists_across_recalculate(self):
        """
        After parcel is ON and kitchen send is simulated (recalculate called),
        parcel_surcharge survives. The kitchen-send path calls recalculate_totals.
        """
        order = self._make_order([(self.idli, 2)])
        self._toggle(order.id)
        order.refresh_from_db()
        saved = order.parcel_surcharge
        order.recalculate_totals()
        order.refresh_from_db()
        self.assertEqual(order.parcel_surcharge, saved)


# ── Hole 2 — toggle-parcel HTTP status determines whether client records apply ──

class ToggleHttpStatusTests(ParcelBase):
    """
    Hole 2 fix: the JS now checks r.ok before setting _parcelAppliedToOrder.

    fetch() only rejects on network failure — it resolves for 404, 500, etc.
    Before the fix, even a 500 from toggle-parcel would set _parcelAppliedToOrder,
    causing the client to skip the parcel toggle on the next dispatch while the
    server still has parcel_surcharge=0.

    After the fix: r.ok (status 200–299) → record applied. Non-2xx → don't record,
    so the next dispatch retries the toggle correctly.

    Python tests verify the HTTP status contract the server upholds — this is
    the foundation the client-side r.ok check depends on.
    """

    def test_successful_toggle_returns_200(self):
        """r.ok=true → client records _parcelAppliedToOrder."""
        order = self._make_order([(self.idli, 2)])
        resp = self._toggle(order.id)
        self.assertEqual(resp.status_code, 200)

    def test_toggle_on_closed_order_returns_non_200(self):
        """r.ok=false → client does NOT record _parcelAppliedToOrder → retries next dispatch."""
        order = self._make_order()
        order.status = "closed"
        order.save(update_fields=["status"])
        resp = self._toggle(order.id)
        self.assertNotEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.status_code, 400)

    def test_toggle_on_nonexistent_order_returns_non_200(self):
        """Nonexistent order → 404 → r.ok=false → client retries."""
        resp = self._toggle(999999)
        self.assertNotEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.status_code, 400)

    def test_successful_toggle_off_also_returns_200(self):
        """Toggling OFF also returns 200 — r.ok=true → client correctly records parcel_on=false."""
        order = self._make_order([(self.idli, 2)])
        self._toggle(order.id)           # ON  → 200
        resp = self._toggle(order.id)    # OFF → 200
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["parcel_on"])

    def test_toggle_response_body_has_parcel_on_flag(self):
        """
        The r.ok check alone is not enough if the body doesn't have parcel_on.
        Verify the 200 response always includes parcel_on so the client can
        distinguish a successful ON from a successful OFF.
        """
        order = self._make_order([(self.idli, 1)])
        d = self._toggle(order.id).json()
        self.assertIn("parcel_on", d)
        self.assertIn("parcel_amount", d)
        self.assertIn("grand_total", d)


# ── 6. Gap 3 — QSR: each new order gets parcel applied exactly once ────────────

class QSRParcelDispatchTests(ParcelBase):
    """
    Gap 3 fix: submitQSROrder now sets _parcelAppliedToOrder = qsrPendingOrderId
    after the await. This prevents a second QSR dispatch from re-toggling.

    QSR always creates a brand-new order, so a different order_id is returned
    each time. The tracking variable must be updated per-order.
    """

    def test_single_toggle_on_new_qsr_order(self):
        order = self._make_order([(self.idli, 4)])
        d = self._toggle(order.id).json()
        self.assertTrue(d["parcel_on"])
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("20"))  # 4 × ₹5

    def test_two_sequential_qsr_orders_each_get_parcel(self):
        """
        Two separate QSR orders (different IDs). Each gets one toggle call.
        _parcelAppliedToOrder from the first must not block the second.
        """
        order1 = self._make_order([(self.idli, 2)])
        order2 = self._make_order([(self.dosa, 3)])
        self.assertNotEqual(order1.id, order2.id)

        self._toggle(order1.id)
        self._toggle(order2.id)

        order1.refresh_from_db()
        order2.refresh_from_db()
        self.assertEqual(order1.parcel_surcharge, Decimal("10"))  # 2 × ₹5
        self.assertEqual(order2.parcel_surcharge, Decimal("15"))  # 3 × ₹5

    def test_qsr_flat_charge_mode(self):
        self.outlet.parcel_charge_per_item = False
        self.outlet.save(update_fields=["parcel_charge_per_item"])
        order = self._make_order([(self.idli, 5)])
        d = self._toggle(order.id).json()
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("5"))  # flat

    def test_qsr_zero_outlet_charge_no_parcel(self):
        self.outlet.parcel_charge_amount = Decimal("0")
        self.outlet.save(update_fields=["parcel_charge_amount"])
        order = self._make_order([(self.idli, 3)])
        d = self._toggle(order.id).json()
        self.assertFalse(d["parcel_on"])
        self.assertEqual(d["parcel_amount"], 0.0)

    def test_qsr_item_override_used_instead_of_outlet_charge(self):
        self.idli.parcel_charge = Decimal("12")
        self.idli.save(update_fields=["parcel_charge"])
        order = self._make_order([(self.idli, 2)])
        d = self._toggle(order.id).json()
        self.assertEqual(Decimal(str(d["parcel_amount"])), Decimal("24"))  # 2 × ₹12


# ── 5. running_order_items carries everything Token Billing's instant token
#      switch needs (tokens/templates/tokens/token_billing.html::selectToken)
#      -- token number, human status, and amount still due, so switching
#      tokens no longer requires a full page reload just to read these off
#      server-rendered template tags. ─────────────────────────────────────────

class RunningOrderTokenAndBillingStateTests(ParcelBase):
    def test_token_display_present_for_a_token_order(self):
        from datetime import date
        from tokens.models import TokenOrder

        order = self._make_order([(self.idli, 1)])
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            token_number=7, date=date.today(), is_online=False,
        )
        d = self._running(order_id=order.id).json()
        self.assertEqual(d["token_display"], "#7")

    def test_token_display_none_for_a_non_token_order(self):
        order = self._make_order([(self.idli, 1)])
        d = self._running(order_id=order.id).json()
        self.assertIsNone(d["token_display"])

    def test_order_status_display_is_human_readable(self):
        order = self._make_order([(self.idli, 1)], status="billing")
        d = self._running(order_id=order.id).json()
        self.assertEqual(d["order_status"], "billing")
        self.assertEqual(d["order_status_display"], order.get_status_display())

    def test_remaining_is_full_total_with_no_payments(self):
        order = self._make_order([(self.dosa, 1)])  # ₹60 + 5% GST
        d = self._running(order_id=order.id).json()
        self.assertEqual(Decimal(str(d["remaining"])), order.grand_total)

    def test_remaining_drops_by_amount_already_paid(self):
        from orders.models import Payment

        order = self._make_order([(self.dosa, 1)])
        Payment.objects.create(
            order=order, method="cash", amount=Decimal("30.00"),
        )
        d = self._running(order_id=order.id).json()
        self.assertEqual(Decimal(str(d["remaining"])), order.grand_total - Decimal("30.00"))
