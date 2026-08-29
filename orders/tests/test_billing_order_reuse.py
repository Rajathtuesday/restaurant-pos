"""
get_or_create_open_order (orders/services/order_service.py) used to only
recognize status="open" orders for a table. Generating a bill moves the
order to status="billing", so the very next "add one more item" for that
table -- whenever the request didn't already carry an order_id, which is
every staff request from billing.html -- silently created a SECOND,
separate order instead of adding to the one being paid, and unconditionally
reset the table back to "ordering" even though it was correctly "billing".

Run: python manage.py test orders.tests.test_billing_order_reuse
"""
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from tenants.models import Tenant, Outlet
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from orders.services.order_service import get_or_create_open_order


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Billing Reuse Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.cashier = User.objects.create_user(
            username="reuse_cashier", password="pwd",
            role="cashier", tenant=self.tenant, outlet=self.outlet,
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.dish = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat, name="Curry", price=Decimal("100"),
        )

    def _order(self, status):
        return Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, status=status,
        )


class GetOrCreateOpenOrderReusesBillingTests(_Base):

    def test_billing_order_is_found_not_duplicated(self):
        billing_order = self._order("billing")
        self.table.state = "billing"
        self.table.save(update_fields=["state"])

        found = get_or_create_open_order(self.cashier, self.table)

        self.assertEqual(found.id, billing_order.id)
        self.assertEqual(Order.objects.filter(table=self.table).count(), 1)

    def test_billing_table_state_is_not_reset_to_ordering(self):
        self._order("billing")
        self.table.state = "billing"
        self.table.save(update_fields=["state"])

        get_or_create_open_order(self.cashier, self.table)

        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "billing")

    def test_paid_order_still_creates_a_fresh_one(self):
        """Regression guard -- a settled table (previous customer paid and
        left) must still start a genuinely new order for the next customer,
        not get stuck reusing a closed-out order."""
        old_order = self._order("paid")
        self.table.state = "free"
        self.table.save(update_fields=["state"])

        new_order = get_or_create_open_order(self.cashier, self.table)

        self.assertNotEqual(new_order.id, old_order.id)
        self.assertEqual(new_order.status, "open")
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "ordering")

    def test_open_order_still_found_as_before(self):
        """Regression guard -- the original, already-correct case."""
        open_order = self._order("open")
        found = get_or_create_open_order(self.cashier, self.table)
        self.assertEqual(found.id, open_order.id)
        self.assertEqual(Order.objects.filter(table=self.table).count(), 1)


class CreateOrderViewReusesBillingOrderTests(_Base):
    """View-level: both the table_id-only fallback (staff, pre-fix) and the
    explicit order_id path (guest QR, already worked) must land the new item
    on the SAME billing-status order, not a duplicate."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.cashier)

    def _post(self, payload):
        return self.client.post(
            reverse("create-order"), data=payload, content_type="application/json",
        )

    def test_table_id_only_reuses_the_billing_order(self):
        billing_order = self._order("billing")
        self.table.state = "billing"
        self.table.save(update_fields=["state"])

        resp = self._post({
            "cart": [{"id": self.dish.id, "quantity": 1}],
            "table_id": self.table.id,
            "source": "dine_in",
        })

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["order_id"], billing_order.id)
        self.assertEqual(Order.objects.filter(table=self.table).count(), 1)
        billing_order.refresh_from_db()
        self.assertEqual(billing_order.items.count(), 1)

    def test_explicit_order_id_reuses_the_billing_order(self):
        billing_order = self._order("billing")
        self.table.state = "billing"
        self.table.save(update_fields=["state"])

        resp = self._post({
            "cart": [{"id": self.dish.id, "quantity": 1}],
            "table_id": self.table.id,
            "order_id": billing_order.id,
            "source": "dine_in",
        })

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["order_id"], billing_order.id)
        self.assertEqual(Order.objects.filter(table=self.table).count(), 1)

    def test_stale_order_id_from_a_closed_previous_order_does_not_block_staff(self):
        """
        billing.html sends order_id whenever it already has one in memory.
        currentOrderId only refreshes when loadRunningOrder() runs (table
        select, or after a submit) -- not the instant a payment completes
        on a different page (bill.html). If a cashier's tab isn't
        reselected after the previous customer's order closes, the next
        customer's first item at this table arrives with a stale order_id
        pointing at that now-closed order. Must not block staff with the
        guest-facing "call a waiter" error -- must fall through and start
        a clean new order instead, same as if no order_id had been sent.
        """
        closed_order = self._order("closed")
        resp = self._post({
            "cart": [{"id": self.dish.id, "quantity": 1}],
            "table_id": self.table.id,
            "order_id": closed_order.id,
            "source": "dine_in",
        })

        self.assertEqual(resp.status_code, 200)
        new_order_id = resp.json()["order_id"]
        self.assertNotEqual(new_order_id, closed_order.id)
        new_order = Order.objects.get(id=new_order_id)
        self.assertEqual(new_order.status, "open")
        self.assertEqual(new_order.items.count(), 1)

    def test_guest_with_a_stale_order_id_is_still_blocked(self):
        """Regression guard -- the guest guardrail (409, "call a waiter")
        must stay exactly as it was. A guest's phone remembering a stale,
        already-billed order_id should still be told to get staff involved,
        not silently handed a fresh order of its own."""
        closed_order = self._order("closed")

        resp = self.client.post(
            reverse("create-order"),
            data={
                "cart": [{"id": self.dish.id, "quantity": 1}],
                "table_token": str(self.table.qr_token),
                "order_id": closed_order.id,
            },
            content_type="application/json",
        )

        self.assertEqual(resp.status_code, 409)
        self.assertIn("call a waiter", resp.json()["error"].lower())
