# tablemerge/tests.py
"""
Moved from orders/tests/test_qr_merged_table.py (Phase 5 of the orders app
split), plus new HTTP-level tests closing a gap found during the move:
merge_tables_view/unmerge_tables_view had zero test coverage anywhere --
only the underlying service functions were exercised, indirectly, via the
QR-ordering tests below.

Run: python manage.py test tablemerge
"""
import json
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tablemerge.models import TableMerge
from tablemerge.services import merge_tables, unmerge_tables
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


# ======================================================================
#  New: HTTP-level coverage for merge_tables_view / unmerge_tables_view.
#  Previously zero coverage anywhere -- only the service functions above
#  were exercised, and only indirectly.
# ======================================================================

class MergeTablesViewTest(_Base):
    def _login(self):
        c = Client()
        c.login(username="merge_mgr", password="pw")
        return c

    def test_manager_can_merge_tables(self):
        resp = self._login().post(
            reverse("merge-tables"),
            data=json.dumps({"primary_table": self.table_a.id, "tables": [self.table_b.id]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        merge_id = resp.json()["merge_id"]
        merge = TableMerge.objects.get(id=merge_id)
        self.assertEqual(merge.primary_table_id, self.table_a.id)
        self.assertIn(self.table_b, merge.tables.all())

    def test_merge_then_unmerge_round_trip_via_http(self):
        client = self._login()
        merge_resp = client.post(
            reverse("merge-tables"),
            data=json.dumps({"primary_table": self.table_a.id, "tables": [self.table_b.id]}),
            content_type="application/json",
        )
        self.assertEqual(merge_resp.status_code, 200)

        unmerge_resp = client.post(reverse("unmerge-tables", args=[self.table_a.id]))
        self.assertEqual(unmerge_resp.status_code, 200)

        merge_id = merge_resp.json()["merge_id"]
        self.assertFalse(TableMerge.objects.get(id=merge_id).is_active)

    def test_unmerge_nonexistent_merge_returns_404(self):
        resp = self._login().post(reverse("unmerge-tables", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_logged_out_user_redirected_not_merged(self):
        resp = Client().post(
            reverse("merge-tables"),
            data=json.dumps({"primary_table": self.table_a.id, "tables": [self.table_b.id]}),
            content_type="application/json",
        )
        self.assertIn(resp.status_code, (302, 401, 403))
        self.assertFalse(TableMerge.objects.filter(primary_table=self.table_a).exists())

    def test_tenant_without_merge_tables_feature_is_blocked(self):
        # franchise/cafe tenants don't get merge_tables by default (confirmed
        # via core/features.py::TENANT_FEATURES) -- a QSR tenant is the
        # natural "feature off" fixture, no TenantFeatureOverride needed.
        qsr_tenant = Tenant.objects.create(name="QSR No Merge", tenant_type="franchise")
        qsr_outlet = Outlet.objects.create(tenant=qsr_tenant, name="Main")
        qsr_manager = User.objects.create_user(
            username="qsr_mgr", password="pw", tenant=qsr_tenant,
            outlet=qsr_outlet, role="manager",
        )
        qsr_table_a = Table.objects.create(tenant=qsr_tenant, outlet=qsr_outlet, name="Q1")
        qsr_table_b = Table.objects.create(tenant=qsr_tenant, outlet=qsr_outlet, name="Q2")

        c = Client()
        c.login(username="qsr_mgr", password="pw")
        resp = c.post(
            reverse("merge-tables"),
            data=json.dumps({"primary_table": qsr_table_a.id, "tables": [qsr_table_b.id]}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
