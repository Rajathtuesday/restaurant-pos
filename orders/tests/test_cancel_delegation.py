"""
Tests proving cancel_item/cancel_order (orders/views/order_actions.py) now
actually reach void_service.void_order_item in production — these are the
live, URL-wired endpoints the waiter UI calls. Before this fix, cancel_item
duplicated voiding logic inline with zero inventory restoration and zero
table-state update; void_order_item had both, correctly, but was dead code.

Run: python manage.py test orders.tests.test_cancel_delegation
"""
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from tenants.models import Tenant, Outlet
from menu.models import MenuCategory, MenuItem
from inventory.models import InventoryItem, Recipe
from orders.models import Order, OrderItem, Table


class CancelDelegationTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Cancel Delegation Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.cashier = User.objects.create_user(
            username="cd_cashier", password="pwd",
            role="cashier", tenant=self.tenant, outlet=self.outlet,
        )
        self.manager = User.objects.create_user(
            username="cd_manager", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet,
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.dish = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat, name="Naan", price=Decimal("60"),
        )
        self.flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Flour", unit="kg", stock=Decimal("10.000"),
        )
        Recipe.objects.create(
            menu_item=self.dish, inventory_item=self.flour,
            quantity_required=Decimal("500"), unit="g",
        )

    def _order(self):
        return Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, status="open",
        )

    def _sent_item(self, order, quantity=1):
        item = OrderItem.objects.create(
            order=order, menu_item=self.dish, quantity=quantity,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60") * quantity, status="sent",
        )
        # Simulate the KOT-time deduction this item would already have caused.
        self.flour.stock = self.flour.stock - Decimal("0.5") * quantity
        self.flour.save(update_fields=["stock"])
        return item

    def test_cashier_cancel_item_restores_inventory(self):
        """Proves the fix reaches production: hitting the real URL (not
        calling void_order_item directly) actually restores stock."""
        order = self._order()
        item = self._sent_item(order)
        client = Client()
        client.force_login(self.cashier)

        resp = client.post(reverse("cancel-item", args=[item.id]))

        self.assertEqual(resp.status_code, 200)
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))

    def test_cancel_item_response_shape_preserved(self):
        order = self._order()
        item1 = self._sent_item(order)
        OrderItem.objects.create(
            order=order, menu_item=self.dish, quantity=1,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="sent",
        )
        order.recalculate_totals()
        client = Client()
        client.force_login(self.cashier)

        resp = client.post(reverse("cancel-item", args=[item1.id]))
        data = resp.json()

        self.assertTrue(data["success"])
        self.assertIn("new_total", data)
        order.refresh_from_db()
        self.assertEqual(Decimal(str(data["new_total"])), order.grand_total)

    def test_cashier_cannot_override_void_served_item(self):
        order = self._order()
        item = self._sent_item(order)
        item.status = "served"
        item.save(update_fields=["status"])
        client = Client()
        client.force_login(self.cashier)

        resp = client.post(reverse("cancel-item", args=[item.id]))

        self.assertEqual(resp.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.status, "served")

    def test_manager_can_override_void_served_item(self):
        order = self._order()
        item = self._sent_item(order)
        item.status = "served"
        item.save(update_fields=["status"])
        client = Client()
        client.force_login(self.manager)

        resp = client.post(reverse("cancel-item", args=[item.id]))

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, "voided")
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "free")

    def test_cancel_item_missing_item_returns_404(self):
        client = Client()
        client.force_login(self.cashier)

        resp = client.post(reverse("cancel-item", args=[999999]))

        self.assertEqual(resp.status_code, 404)

    def test_cancel_order_restores_inventory_and_skips_served_item(self):
        """Whole-order cancel: the sent item is voided with inventory
        restored, the already-served item is left untouched (existing
        behavior), and the table is freed."""
        order = self._order()
        sent_item = self._sent_item(order)
        served_item = self._sent_item(order)
        served_item.status = "served"
        served_item.save(update_fields=["status"])
        client = Client()
        client.force_login(self.cashier)

        resp = client.post(reverse("cancel-order", args=[order.id]))

        self.assertEqual(resp.status_code, 200)
        sent_item.refresh_from_db()
        served_item.refresh_from_db()
        self.assertEqual(sent_item.status, "voided")
        self.assertEqual(served_item.status, "served")  # untouched

        self.flour.refresh_from_db()
        # Only the sent item's 0.5kg is restored — the served item's
        # deduction stays gone, matching existing cancel_order behavior.
        self.assertEqual(self.flour.stock, Decimal("9.500"))

        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "free")
