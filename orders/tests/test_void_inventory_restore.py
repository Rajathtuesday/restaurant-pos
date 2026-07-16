"""
Tests for void_service._restore_inventory_for_void.

Voiding a dispatched item must reverse the exact deduction that happened
when the KOT was sent — same unit conversion, same modifier handling as
deduct_inventory_for_items. Before this fix, restoration used the raw
recipe quantity with no conversion (over/under-restoring by whatever the
unit mismatch was) and never restored modifier-linked inventory at all.

Run: python manage.py test orders.tests.test_void_inventory_restore
"""
from decimal import Decimal
from django.test import TestCase

from accounts.models import User
from tenants.models import Tenant, Outlet
from menu.models import MenuCategory, MenuItem, ModifierGroup, Modifier
from inventory.models import InventoryItem, Recipe, ModifierRecipe
from orders.models import Order, OrderItem, OrderItemModifier, Table
from orders.services.void_service import void_order_item


class VoidInventoryRestoreTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Void Restore Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(
            username="void_mgr", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet,
        )
        self.cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat, name="Naan", price=Decimal("60"),
        )
        # Tracked in KG — recipe written in grams, so a naive restore would
        # be off by 1000x.
        self.flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Flour", unit="kg", stock=Decimal("10.000"),
        )
        Recipe.objects.create(
            menu_item=self.naan, inventory_item=self.flour,
            quantity_required=Decimal("500"), unit="g",
        )

    def _order_with_sent_item(self, quantity=2):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
        item = OrderItem.objects.create(
            order=order, menu_item=self.naan, quantity=quantity,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60") * quantity, status="sent",
        )
        # Simulate the deduction that happened at KOT-send time, so the
        # starting stock reflects what a real void would need to reverse.
        self.flour.stock = self.flour.stock - Decimal("0.5") * quantity
        self.flour.save(update_fields=["stock"])
        return order, item

    def test_restore_converts_gram_recipe_against_kg_item(self):
        """2 Naans sent (0.5kg × 2 = 1.0kg deducted, stock now 9.0kg). Voiding
        must restore exactly 1.0kg — the pre-fix bug would have restored 1000
        (reading grams as kg) or 0.001 depending on direction."""
        order, item = self._order_with_sent_item(quantity=2)
        self.assertEqual(self.flour.stock, Decimal("9.000"))

        void_order_item(self.manager, item.id, "wrong order")

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))

    def test_pending_item_void_does_not_restore(self):
        """An item never sent to the kitchen never had stock deducted, so
        voiding it must not touch inventory at all."""
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
        item = OrderItem.objects.create(
            order=order, menu_item=self.naan, quantity=1,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="pending",
        )
        stock_before = self.flour.stock

        void_order_item(self.manager, item.id, "changed mind")

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, stock_before)

    def test_modifier_linked_inventory_is_restored(self):
        """Previously: modifier-driven deduction (e.g. Extra Cheese) was
        never reversed on void at all — a permanent, silent stock loss on
        every voided item that had a costed modifier."""
        cheese = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Cheese", unit="g", stock=Decimal("500"),
        )
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        mod = Modifier.objects.create(group=group, name="Extra Cheese", price=Decimal("20"))
        ModifierRecipe.objects.create(
            modifier=mod, inventory_item=cheese,
            quantity_required=Decimal("30"), unit="g",
        )

        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
        item = OrderItem.objects.create(
            order=order, menu_item=self.naan, quantity=1,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="sent",
        )
        OrderItemModifier.objects.create(order_item=item, modifier=mod, name=mod.name, price=mod.price)
        # Simulate the KOT-time deduction for both the base recipe and the modifier.
        self.flour.stock = self.flour.stock - Decimal("0.5")
        self.flour.save(update_fields=["stock"])
        cheese.stock = cheese.stock - Decimal("30")
        cheese.save(update_fields=["stock"])

        void_order_item(self.manager, item.id, "customer changed order")

        self.flour.refresh_from_db()
        cheese.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))
        self.assertEqual(cheese.stock, Decimal("500"))

    def test_incompatible_recipe_unit_is_not_restored_as_garbage(self):
        """A recipe with an incompatible unit must be skipped, not restore a
        meaningless number, same fail-safe behavior as the deduction path."""
        bad_item = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Butter", unit="kg", stock=Decimal("5"),
        )
        Recipe.objects.create(
            menu_item=self.naan, inventory_item=bad_item,
            quantity_required=Decimal("2"), unit="pcs",  # incompatible with kg
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
        item = OrderItem.objects.create(
            order=order, menu_item=self.naan, quantity=1,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="sent",
        )

        void_order_item(self.manager, item.id, "test")  # must not raise

        bad_item.refresh_from_db()
        self.assertEqual(bad_item.stock, Decimal("5"))  # unchanged, not corrupted

    def test_voiding_last_item_updates_table_state(self):
        """void_order_item previously never touched table state at all —
        voiding the last active item on a table must free it."""
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, table=table, status="open")
        item = OrderItem.objects.create(
            order=order, menu_item=self.naan, quantity=1,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="sent",
        )

        void_order_item(self.manager, item.id, "test")

        table.refresh_from_db()
        self.assertEqual(table.state, "free")

    def test_voiding_review_item_does_not_restore_inventory(self):
        """A "review" item (QR guest item awaiting staff approval) has never
        been sent to the kitchen, so it's never had inventory deducted —
        voiding it must not add phantom stock that was never taken."""
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
        item = OrderItem.objects.create(
            order=order, menu_item=self.naan, quantity=2,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("120"), status="review",
        )
        stock_before = self.flour.stock

        void_order_item(self.manager, item.id, "rejected")

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, stock_before)
