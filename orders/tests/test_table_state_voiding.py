"""
Tests for update_table_state (orders/services/order_service.py) and the
matching computation in tables_data (orders/views/table_views.py).

Bug being fixed: a table with an order whose items were all cancelled
never went back to "free" — voided items weren't excluded from the status
checks, so a fully-voided order fell through every branch into a catch-all
that set "ordering". A mixed served+voided order (e.g. a QR customer's
first round served, a cancelled reorder voided) had the same problem.

Run: python manage.py test orders.tests.test_table_state_voiding
"""
from decimal import Decimal
from django.test import TestCase

from accounts.models import User
from tenants.models import Tenant, Outlet
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from orders.services.order_service import update_table_state


class UpdateTableStateVoidingTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Table State Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(
            username="tablestate_mgr", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet,
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.dish = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat, name="Curry", price=Decimal("100"),
        )

    def _order(self):
        return Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, status="open",
        )

    def _item(self, order, status):
        return OrderItem.objects.create(
            order=order, menu_item=self.dish, quantity=1,
            price=Decimal("100"), gst_percentage=Decimal("0"),
            total_price=Decimal("100"), status=status,
        )

    def test_all_items_voided_frees_table(self):
        order = self._order()
        self._item(order, "voided")
        self._item(order, "voided")

        update_table_state(order)

        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "free")

    def test_served_plus_voided_reads_ready_not_stuck(self):
        """The multi-round QR scenario: round-1 item served, a cancelled
        round-2 reorder voided. Must read as ready-to-bill, not stuck."""
        order = self._order()
        self._item(order, "served")
        self._item(order, "voided")

        update_table_state(order)

        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "ready")

    def test_review_only_item_voided_frees_table(self):
        order = self._order()
        self._item(order, "review")
        item = order.items.first()
        item.status = "voided"
        item.save(update_fields=["status"])

        update_table_state(order)

        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "free")

    def test_review_only_unvoided_reads_ordering_not_preparing(self):
        order = self._order()
        self._item(order, "review")

        update_table_state(order)

        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "ordering")

    def test_billing_state_not_clobbered(self):
        self.table.state = "billing"
        self.table.save(update_fields=["state"])
        order = self._order()
        self._item(order, "served")

        update_table_state(order)

        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "billing")

    def test_cleaning_state_not_clobbered(self):
        self.table.state = "cleaning"
        self.table.save(update_fields=["state"])
        order = self._order()
        self._item(order, "served")

        update_table_state(order)

        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "cleaning")

    def test_sent_item_reads_preparing_even_with_voided_sibling(self):
        order = self._order()
        self._item(order, "sent")
        self._item(order, "voided")

        update_table_state(order)

        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "preparing")


class TablesDataVoidingTests(TestCase):
    """Same root-cause fix, applied to the independent status computation
    that actually drives the live tables dashboard."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Dashboard Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="dash_owner", password="pwd",
            role="owner", tenant=self.tenant, outlet=self.outlet,
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T2")
        self.cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.dish = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat, name="Curry", price=Decimal("100"),
        )
        self.client.force_login(self.owner)

    def _order_with_items(self, statuses):
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, status="open",
        )
        for status in statuses:
            OrderItem.objects.create(
                order=order, menu_item=self.dish, quantity=1,
                price=Decimal("100"), gst_percentage=Decimal("0"),
                total_price=Decimal("100"), status=status,
            )
        return order

    def test_all_voided_reads_free(self):
        self._order_with_items(["voided", "voided"])

        resp = self.client.get("/tables-data/")
        row = next(r for r in resp.json()["tables"] if r["id"] == self.table.id)

        self.assertEqual(row["status"], "free")

    def test_served_plus_voided_reads_served(self):
        self._order_with_items(["served", "voided"])

        resp = self.client.get("/tables-data/")
        row = next(r for r in resp.json()["tables"] if r["id"] == self.table.id)

        self.assertEqual(row["status"], "served")
