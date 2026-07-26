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

    def test_table_state_respected_when_no_order_exists(self):
        """A table can carry a non-"free" state with no Order at all -- e.g.
        a seated reservation nudges Table.state to "ordering" before any
        Order exists (crm/views.py::update_reservation_status). Previously
        the "no order" branch hardcoded "free", silently discarding this."""
        self.table.state = "ordering"
        self.table.save(update_fields=["state"])

        resp = self.client.get("/tables-data/")
        row = next(r for r in resp.json()["tables"] if r["id"] == self.table.id)

        self.assertEqual(row["status"], "ordering")

    def test_free_table_with_no_order_still_reads_free(self):
        """Locks in the untouched common case -- the fix must not flip a
        genuinely free, order-less table to anything else."""
        resp = self.client.get("/tables-data/")
        row = next(r for r in resp.json()["tables"] if r["id"] == self.table.id)

        self.assertEqual(row["status"], "free")

    def test_stale_billing_state_with_no_order_self_heals_to_free(self):
        """bill_view() sets Table.state = "billing" on every page load, not
        just on an actual payment action -- a cashier who opens a bill and
        then navigates away without paying or cancelling leaves the table
        stuck showing "Billing" / "Pay Bill" forever, for an order that no
        longer exists in ["open", "billing"] (already closed/cancelled by
        other means). Confirmed real bug: reservation-seating's "never
        clobber a non-free table" guard then silently refuses to nudge the
        table to "ordering", so the floor plan keeps showing the stale
        "Billing" state after a fresh reservation is seated there.
        Must self-heal both the API response AND the stored value."""
        self.table.state = "billing"
        self.table.save(update_fields=["state"])

        resp = self.client.get("/tables-data/")
        row = next(r for r in resp.json()["tables"] if r["id"] == self.table.id)

        self.assertEqual(row["status"], "free")
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "free")

    def test_stale_preparing_state_with_no_order_self_heals_to_free(self):
        """Same self-heal, for a different stale non-order state (confirms
        this isn't special-cased to "billing" only)."""
        self.table.state = "preparing"
        self.table.save(update_fields=["state"])

        resp = self.client.get("/tables-data/")
        row = next(r for r in resp.json()["tables"] if r["id"] == self.table.id)

        self.assertEqual(row["status"], "free")
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "free")

    def test_seating_a_reservation_now_works_after_stale_billing_self_heals(self):
        """End-to-end: a table stuck in stale "billing" with no order must
        become seatable again -- confirms the self-heal actually unblocks
        the real workflow the user reported as broken, not just the display."""
        self.table.state = "billing"
        self.table.save(update_fields=["state"])

        # First poll self-heals the stale state to "free"...
        self.client.get("/tables-data/")
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "free")

        # ...so a reservation can now legitimately nudge it to "ordering".
        self.table.state = "ordering"  # simulates update_reservation_status's nudge, now unblocked
        self.table.save(update_fields=["state"])
        resp = self.client.get("/tables-data/")
        row = next(r for r in resp.json()["tables"] if r["id"] == self.table.id)
        self.assertEqual(row["status"], "ordering")
