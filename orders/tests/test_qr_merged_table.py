# orders/tests/test_qr_merged_table.py
"""
Bug: when two tables are merged via the floor plan, a guest scanning the QR
code stuck on a SECONDARY merged table got an order created against that
table alone instead of the merged group's primary table — the QR path
(orders/views/billing_views.py::create_order) never checked for an active
TableMerge at all, unlike the waiter-side flows (billing_core.py,
order_views.py), which each already resolved it inline.

Fix: orders/services/table_merge_service.py::resolve_primary_table(), called
from create_order right after the table is identified (both the table_token
QR branch and the staff table_id branch).

Run: python manage.py test orders.tests.test_qr_merged_table
"""
import json
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from orders.services.table_merge_service import merge_tables, unmerge_tables
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Merge QR Tenant", slug="merge-qr-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(
            username="merge_mgr", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="manager",
        )
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Butter Naan", price=Decimal("60.00"),
        )
        self.table_a = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="A1", is_active=True
        )
        self.table_b = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="B1", is_active=True
        )

    def _place(self, table, item, qty=1, order_id=None):
        payload = {
            "table_token": str(table.qr_token),
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


class QrOrderRespectsTableMergeTest(_Base):
    def test_qr_order_at_secondary_merged_table_lands_on_primary(self):
        merge_tables(self.manager, self.table_a.id, [self.table_b.id])

        resp = self._place(self.table_b, self.item)
        self.assertEqual(resp.status_code, 200)

        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.table_id, self.table_a.id)
        self.assertEqual(
            Order.objects.filter(table=self.table_b, status="open").count(), 0,
        )

    def test_qr_order_at_primary_merged_table_is_unaffected(self):
        merge_tables(self.manager, self.table_a.id, [self.table_b.id])

        resp = self._place(self.table_a, self.item)
        self.assertEqual(resp.status_code, 200)

        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.table_id, self.table_a.id)

    def test_qr_reorder_second_round_at_secondary_table_still_merges_correctly(self):
        merge = merge_tables(self.manager, self.table_a.id, [self.table_b.id])

        first = self._place(self.table_b, self.item)
        order_id = first.json()["order_id"]

        second = self._place(self.table_b, self.item, qty=2, order_id=order_id)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["order_id"], order_id)

        order = Order.objects.get(id=order_id)
        self.assertEqual(order.table_id, self.table_a.id)
        self.assertEqual(order.items.count(), 2)

    def test_unmerged_table_qr_order_behaves_as_before(self):
        # No merge in effect at all — must be a complete no-op.
        resp = self._place(self.table_a, self.item)
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.table_id, self.table_a.id)

    def test_qr_order_after_unmerge_goes_back_to_individual_table(self):
        merge = merge_tables(self.manager, self.table_a.id, [self.table_b.id])
        unmerge_tables(self.manager, merge.id)

        resp = self._place(self.table_b, self.item)
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.table_id, self.table_b.id)
