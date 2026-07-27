"""
Tests for the outlet-wide "Counter / Walk-in" QR (Outlet.qr_token).

Background: create_order's QR-guest path only ever resolved a Table by
qr_token, so a QSR/cafe outlet with no seating (nothing to hang a
per-table QR on) had no way to accept QR orders at all. Outlet.qr_token
gives such outlets one static QR for the whole counter; orders placed
through it get table=None, same as any other walk-in order.

The one thing that needed real care: a tableless guest's order_id can't be
trusted the way a table-scoped guest's can, because the counter QR token
is shared by every customer (unlike a table's, which is scoped to whoever
is physically sitting there) -- so a stored order_id from an earlier visit
must never let a guest silently attach items to someone else's open order.

Run: python manage.py test orders.tests.test_counter_qr
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from menu.models import MenuCategory, MenuItem
from orders.models import Order
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Counter Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Snacks"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Samosa", price=Decimal("30.00"),
        )

    def _place(self, item, qty=1, order_id=None):
        payload = {
            "table_token": str(self.outlet.qr_token),
            "cart": [{"id": item.id, "quantity": qty}],
            "source": "web",
        }
        if order_id:
            payload["order_id"] = order_id
        return self.client.post(
            reverse("create-order"),
            data=json.dumps(payload),
            content_type="application/json",
        )


class CounterOrderCreationTest(_Base):
    def test_outlet_qr_token_creates_tableless_order(self):
        resp = self._place(self.item, qty=2)
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertIsNone(order.table)
        self.assertEqual(order.tenant_id, self.tenant.id)
        self.assertEqual(order.outlet_id, self.outlet.id)

    def test_cafe_tenant_still_gets_a_token_for_counter_order(self):
        resp = self._place(self.item)
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertTrue(hasattr(order, "token"))
        self.assertIsNotNone(order.token.token_number)

    def test_unknown_qr_token_404s(self):
        import uuid
        payload = {
            "table_token": str(uuid.uuid4()),
            "cart": [{"id": self.item.id, "quantity": 1}],
            "source": "web",
        }
        resp = self.client.post(
            reverse("create-order"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


class CounterGuestReorderIsolationTest(_Base):
    """
    A tableless guest passing a stale/guessed order_id must never merge
    into that order -- there's no physical-table boundary to verify
    ownership with on a shared counter QR, so every submission from a
    tableless guest becomes its own fresh order instead.
    """

    def test_passing_someone_elses_order_id_creates_a_fresh_order_instead(self):
        first = self._place(self.item, qty=1)
        first_order_id = first.json()["order_id"]

        second = self._place(self.item, qty=1, order_id=first_order_id)
        self.assertEqual(second.status_code, 200)
        second_order_id = second.json()["order_id"]

        self.assertNotEqual(first_order_id, second_order_id)
        first_order = Order.objects.get(id=first_order_id)
        self.assertEqual(first_order.items.count(), 1)
