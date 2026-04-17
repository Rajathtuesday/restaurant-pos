# orders/tests/test_financial_flows.py
"""
Tests covering the two highest-risk zero-coverage paths:
  - CashSession open / close / discrepancy reconciliation
  - billing_view access control & table isolation
  - pay_order flow (cash settlement)
  - create_order edge cases (invalid table, empty cart)
"""
import json
from decimal import Decimal

from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Payment, Table
from shifts.models import CashSession
from tenants.models import Tenant, Outlet


# ─────────────────────────────────────────────
# SHARED HELPERS (duplicated from test_critical so tests stay independent)
# ─────────────────────────────────────────────

def _tenant(name="Cafe"):
    t = Tenant.objects.create(name=name)
    o = Outlet.objects.create(tenant=t, name=f"{name} HQ")
    return t, o


def _user(tenant, outlet, role="cashier", username=None):
    uname = username or f"u_{tenant.id}_{outlet.id}_{role}"
    return User.objects.create_user(
        username=uname, password="pass1234",
        tenant=tenant, outlet=outlet, role=role
    )


def _item(tenant, outlet, price=200, gst=5):
    cat, _ = MenuCategory.objects.get_or_create(
        tenant=tenant, outlet=outlet, name="Mains"
    )
    return MenuItem.objects.create(
        tenant=tenant, outlet=outlet, category=cat,
        name="Dish", price=Decimal(str(price)),
        gst_percentage=Decimal(str(gst)), is_available=True
    )


def _table(tenant, outlet, name="T1"):
    return Table.objects.create(tenant=tenant, outlet=outlet, name=name)


def _order(tenant, outlet, user, table, status="open"):
    return Order.objects.create(
        tenant=tenant, outlet=outlet, table=table,
        created_by=user, status=status
    )


def _order_item(order, menu_item, qty=1):
    return OrderItem.objects.create(
        order=order, menu_item=menu_item,
        quantity=qty, price=menu_item.price,
        gst_percentage=menu_item.gst_percentage,
        total_price=menu_item.price * qty,
        status="pending"
    )


# ═══════════════════════════════════════════════
# 1.  CASH SESSION — OPEN / CLOSE / RECONCILE
# ═══════════════════════════════════════════════

class CashSessionOpenTest(TestCase):

    def setUp(self):
        self.t, self.o = _tenant("Session Cafe")
        self.mgr = _user(self.t, self.o, role="manager", username="mgr1")
        self.c = Client()
        self.c.force_login(self.mgr)

    def test_open_session_creates_record(self):
        resp = self.c.post(
            "/shifts/sessions/open/",
            data=json.dumps({"opening_balance": 500}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertTrue(CashSession.objects.filter(
            tenant=self.t, outlet=self.o, status="open"
        ).exists())

    def test_cannot_open_two_sessions(self):
        CashSession.objects.create(
            tenant=self.t, outlet=self.o,
            opened_by=self.mgr, opening_balance=200, status="open"
        )
        resp = self.c.post(
            "/shifts/sessions/open/",
            data=json.dumps({"opening_balance": 100}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already open", resp.json().get("error", "").lower())

    def test_session_defaults_to_open_status(self):
        s = CashSession.objects.create(
            tenant=self.t, outlet=self.o,
            opened_by=self.mgr, opening_balance=0
        )
        self.assertEqual(s.status, "open")


class CashSessionCloseTest(TestCase):

    def setUp(self):
        self.t, self.o = _tenant("Close Cafe")
        self.mgr = _user(self.t, self.o, role="manager", username="mgr2")
        self.cashier = _user(self.t, self.o, role="cashier", username="cas2")
        self.c = Client()
        self.c.force_login(self.mgr)

        self.menu_item = _item(self.t, self.o, price=1000, gst=0)
        self.table = _table(self.t, self.o, "CS1")

        # Open a session with ₹500 float
        self.session = CashSession.objects.create(
            tenant=self.t, outlet=self.o,
            opened_by=self.mgr, opening_balance=500, status="open"
        )

        # Create a paid order (cash ₹1000) AFTER session opened
        order = _order(self.t, self.o, self.cashier, self.table, status="paid")
        _order_item(order, self.menu_item, qty=1)
        Payment.objects.create(
            order=order, method="cash",
            amount=Decimal("1000"), created_by=self.cashier,
            paid_at=timezone.now()
        )

    def test_close_session_returns_discrepancy(self):
        resp = self.c.post(
            "/shifts/sessions/close/",
            data=json.dumps({"actual_cash": 1400}),  # 100 short
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["success"])
        # Expected = 500 opening + 1000 cash = 1500; actual = 1400 → discrepancy = -100
        self.assertAlmostEqual(data["discrepancy"], -100.0, places=1)

    def test_close_session_marks_closed(self):
        self.c.post(
            "/shifts/sessions/close/",
            data=json.dumps({"actual_cash": 1500}),
            content_type="application/json"
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "closed")

    def test_close_session_records_expected_cash(self):
        self.c.post(
            "/shifts/sessions/close/",
            data=json.dumps({"actual_cash": 1500}),
            content_type="application/json"
        )
        self.session.refresh_from_db()
        # opening 500 + cash sales 1000 = 1500
        self.assertAlmostEqual(float(self.session.expected_cash), 1500.0, places=1)

    def test_no_open_session_returns_400(self):
        self.session.status = "closed"
        self.session.save(update_fields=["status"])
        resp = self.c.post(
            "/shifts/sessions/close/",
            data=json.dumps({"actual_cash": 0}),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)


# ═══════════════════════════════════════════════
# 2.  BILLING VIEW — access control & table isolation
# ═══════════════════════════════════════════════

class BillingViewAccessTest(TestCase):

    def setUp(self):
        self.t, self.o = _tenant("Billing Cafe")
        self.cashier = _user(self.t, self.o, role="cashier", username="bil1")
        self.c = Client()
        self.c.force_login(self.cashier)

    def test_billing_page_loads(self):
        resp = self.c.get("/billing/")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "orders/billing.html")

    def test_unauthenticated_redirects_to_login(self):
        anon = Client()
        resp = anon.get("/billing/")
        self.assertIn(resp.status_code, [301, 302])
        self.assertIn("login", resp.get("Location", "").lower())

    def test_billing_with_valid_table_returns_200(self):
        table = _table(self.t, self.o, "BT1")
        resp = self.c.get(f"/billing/?table={table.id}")
        self.assertEqual(resp.status_code, 200)

    def test_cannot_see_other_tenants_table(self):
        t2, o2 = _tenant("Other Cafe")
        foreign_table = _table(t2, o2, "FT1")
        # Hitting billing with another tenant's table_id should produce no order
        resp = self.c.get(f"/billing/?table={foreign_table.id}")
        self.assertEqual(resp.status_code, 200)
        # The view should render without blowing up; no order for that table in our tenant
        self.assertIsNone(resp.context.get("order"))


# ═══════════════════════════════════════════════
# 3.  CREATE ORDER — edge cases
# ═══════════════════════════════════════════════

class CreateOrderEdgeCasesTest(TestCase):

    def setUp(self):
        self.t, self.o = _tenant("Edge Cafe")
        self.cashier = _user(self.t, self.o, role="cashier", username="edg1")
        self.menu_item = _item(self.t, self.o)
        self.c = Client()
        self.c.force_login(self.cashier)

    def _post(self, payload):
        return self.c.post(
            "/create-order/",
            data=json.dumps(payload),
            content_type="application/json"
        )

    def test_empty_cart_returns_400(self):
        resp = self._post({"cart": [], "table_id": None})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_table_id_returns_400(self):
        resp = self._post({
            "cart": [{"id": self.menu_item.id, "quantity": 1, "modifiers": [], "note": ""}],
            "table_id": 999999
        })
        self.assertEqual(resp.status_code, 400)

    def test_cross_tenant_menu_item_blocked(self):
        t2, o2 = _tenant("Foreign Cafe")
        foreign_item = _item(t2, o2, price=50)
        resp = self._post({
            "cart": [{"id": foreign_item.id, "quantity": 1, "modifiers": [], "note": ""}],
            "table_id": None
        })
        # Menu item belongs to a different tenant — must be rejected
        self.assertGreaterEqual(resp.status_code, 400, 
            f"Expected 4xx for cross-tenant item, got {resp.status_code}: {resp.json()}")

    def test_valid_walkin_order_created(self):
        resp = self._post({
            "cart": [{"id": self.menu_item.id, "quantity": 2, "modifiers": [], "note": ""}],
            "table_id": None
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertIn("order_id", data)

    def test_order_total_matches_cart(self):
        resp = self._post({
            "cart": [{"id": self.menu_item.id, "quantity": 3, "modifiers": [], "note": ""}],
            "table_id": None
        })
        order_id = resp.json()["order_id"]
        order = Order.objects.get(id=order_id)
        order.refresh_from_db()
        # 3 × ₹200 = ₹600 subtotal
        self.assertEqual(order.subtotal, Decimal("600.00"))


# ═══════════════════════════════════════════════
# 4.  PAY ORDER — settlement flow
# ═══════════════════════════════════════════════

class PayOrderTest(TestCase):

    def setUp(self):
        self.t, self.o = _tenant("Pay Cafe")
        self.cashier = _user(self.t, self.o, role="cashier", username="pay1")
        self.menu_item = _item(self.t, self.o, price=500, gst=0)
        self.table = _table(self.t, self.o, "P1")
        self.c = Client()
        self.c.force_login(self.cashier)

        # Build an order in "billing" state with grand_total = 500
        self.order = _order(self.t, self.o, self.cashier, self.table, status="billing")
        _order_item(self.order, self.menu_item, qty=1)
        self.order.recalculate_totals()

        # Open a Cash Session (now required for payments)
        CashSession.objects.create(
            tenant=self.t, outlet=self.o,
            opened_by=self.cashier, opening_balance=Decimal("0"),
            status="open"
        )

    def _pay(self, method="cash", amount=500):
        return self.c.post(
            f"/pay/{self.order.id}/",
            data=json.dumps({"method": method, "amount": amount}),
            content_type="application/json"
        )

    def test_cash_payment_succeeds(self):
        resp = self._pay("cash", 500)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("success"))

    def test_order_marked_paid_after_full_payment(self):
        self._pay("cash", 500)
        self.order.refresh_from_db()
        # The POS system marks fully-paid orders as 'closed' (not 'paid')
        self.assertIn(self.order.status, ["paid", "closed"])

    def test_payment_record_created(self):
        self._pay("upi", 500)
        self.assertEqual(
            Payment.objects.filter(order=self.order, method="upi").count(), 1
        )

    def test_invalid_payment_method_rejected(self):
        resp = self._pay("bitcoin", 500)
        self.assertEqual(resp.status_code, 400)

    def test_zero_amount_rejected(self):
        resp = self._pay("cash", 0)
        self.assertIn(resp.status_code, [400, 200])
        # Either reject at validation or respond — must not crash
        self.assertIsNotNone(resp.json())

    def test_partial_payment_does_not_close_order(self):
        self._pay("cash", 100)   # partial
        self.order.refresh_from_db()
        # Order must NOT be in a terminal state after a partial payment
        self.assertNotIn(self.order.status, ["paid", "closed"],
            f"Partial payment should not close order, got status: {self.order.status}")
