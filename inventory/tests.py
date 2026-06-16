#inventory/tests.py
from django.test import TestCase, Client, override_settings
from decimal import Decimal
from django.urls import reverse
from django.contrib.auth import get_user_model

from tenants.models import Tenant, Outlet
from inventory.models import InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem

_NO_MANIFEST = override_settings(STORAGES={
    "default":     {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})


class InventoryTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        
        self.supplier = Supplier.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Test Supplier"
        )

        self.item = InventoryItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Cheese",
            unit="kg",
            stock=Decimal("10.000"),
            low_stock_threshold=Decimal("2.000"),
            cost_price=Decimal("15.00")
        )

    def test_reduce_stock(self):
        self.item.reduce_stock(Decimal("3.000"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, Decimal("7.000"))

    def test_add_stock(self):
        self.item.add_stock(Decimal("5.000"))
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, Decimal("15.000"))

    def test_low_stock_flag(self):
        self.item.reduce_stock(Decimal("9.000"))
        self.item.refresh_from_db()
        self.assertTrue(self.item.is_low_stock)

    def test_no_auto_po_without_supplier(self):
        # Even if stock goes low, no PO because no supplier and reorder_quantity is 0
        self.item.reduce_stock(Decimal("9.000"))
        self.item.refresh_from_db()
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_auto_po_generation(self):
        # Set preferred supplier and reorder qty
        self.item.preferred_supplier = self.supplier
        self.item.reorder_quantity = Decimal("10.000")
        self.item.save()

        # Reduce stock below threshold (10 -> 1)
        self.item.reduce_stock(Decimal("9.000"))
        self.item.refresh_from_db()

        # Should generate a PO
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        po = PurchaseOrder.objects.first()
        self.assertEqual(po.supplier, self.supplier)
        self.assertEqual(po.status, "draft")
        self.assertTrue(po.po_number.startswith("PO-"))

        # Verify item was added to PO
        self.assertEqual(po.items.count(), 1)
        po_item = po.items.first()
        self.assertEqual(po_item.item, self.item)
        self.assertEqual(po_item.quantity, Decimal("10.000"))
        self.assertEqual(po_item.unit_price, Decimal("15.00"))

    def test_receive_purchase_order_model(self):
        # Create a PO manually
        po = PurchaseOrder.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            supplier=self.supplier,
            status="ordered"
        )
        PurchaseOrderItem.objects.create(
            purchase_order=po,
            item=self.item,
            quantity=Decimal("5.000"),
            unit_price=Decimal("12.00")
        )

        initial_stock = self.item.stock
        po.receive_order()
        
        self.item.refresh_from_db()
        
        # Stock should increase by 5
        self.assertEqual(self.item.stock, initial_stock + Decimal("5.000"))
        # Last purchase price should be updated
        self.assertEqual(self.item.last_purchase_price, Decimal("12.00"))
        # PO status should be received
        po.refresh_from_db()
        self.assertEqual(po.status, "received")
        self.assertIsNotNone(po.received_at)


class InventoryViewsTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant View")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main View")
        
        User = get_user_model()
        self.user = User.objects.create_user(
            username="manager",
            password="pwd",
            role="manager",
            tenant=self.tenant,
            outlet=self.outlet
        )
        
        self.supplier = Supplier.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Supplier View"
        )
        
        self.item = InventoryItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Milk",
            unit="l",
            stock=Decimal("10.000")
        )
        
        self.po = PurchaseOrder.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            supplier=self.supplier,
            status="ordered"
        )
        
        PurchaseOrderItem.objects.create(
            purchase_order=self.po,
            item=self.item,
            quantity=Decimal("20.000"),
            unit_price=Decimal("5.00")
        )
        
        self.client.force_login(self.user)

    def test_receive_po_view_transaction(self):
        # This will verify that `select_for_update()` does not throw an error outside of an atomic block
        # due to our recent fix in views.py
        response = self.client.post(reverse("po_receive", args=[self.po.id]))
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True, "status": "received"})
        
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, "received")
        
        self.item.refresh_from_db()
        self.assertEqual(self.item.stock, Decimal("30.000")) # 10 + 20


@_NO_MANIFEST
class InventoryAccessControlTests(TestCase):
    """Verify that only owners/managers can access the inventory board."""

    def setUp(self):
        User = get_user_model()
        self.tenant = Tenant.objects.create(name="Access Ctrl Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Access Ctrl Outlet")

        self.owner = User.objects.create_user(
            username="inv_owner",
            password="pwd",
            role="owner",
            tenant=self.tenant,
            outlet=self.outlet
        )

        self.waiter = User.objects.create_user(
            username="inv_waiter",
            password="pwd",
            role="waiter",
            tenant=self.tenant,
            outlet=self.outlet
        )

    def test_owner_can_access_inventory_board(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("inventory_board"))
        self.assertEqual(response.status_code, 200)

    def test_waiter_cannot_access_inventory_board(self):
        self.client.force_login(self.waiter)
        response = self.client.get(reverse("inventory_board"))
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_redirected_from_inventory(self):
        response = self.client.get(reverse("inventory_board"))
        self.assertIn(response.status_code, [301, 302])


class InventoryItemFieldTests(TestCase):
    """Verify InventoryItem fields are stored and retrieved correctly."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Field Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Field Outlet")

    def test_item_created_with_correct_fields(self):
        item = InventoryItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Tomato",
            unit="kg",
            stock=Decimal("5.000"),
            low_stock_threshold=Decimal("1.000"),
            cost_price=Decimal("20.00")
        )
        self.assertEqual(item.name, "Tomato")
        self.assertEqual(item.unit, "kg")
        self.assertEqual(item.stock, Decimal("5.000"))
        self.assertEqual(item.cost_price, Decimal("20.00"))

    def test_item_not_low_stock_when_above_threshold(self):
        item = InventoryItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Salt",
            unit="kg",
            stock=Decimal("10.000"),
            low_stock_threshold=Decimal("2.000")
        )
        self.assertFalse(item.is_low_stock)


# ============================================================
# WASTAGE LOGGING TESTS
# ============================================================

class WastageViewTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Bar Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Bar")
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="barmanager", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet
        )
        self.waiter = User.objects.create_user(
            username="barwaiter", password="pwd",
            role="waiter", tenant=self.tenant, outlet=self.outlet
        )
        self.rum = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Bacardi Rum", unit="ml",
            stock=Decimal("750.000"),
            low_stock_threshold=Decimal("100.000"),
        )
        self.client.force_login(self.manager)

    def _post(self, item_id, payload):
        import json
        return self.client.post(
            reverse("inventory_log_wastage", args=[item_id]),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_wastage_deducts_stock(self):
        resp = self._post(self.rum.id, {"quantity": "30", "reason": "Spillage"})
        self.assertEqual(resp.status_code, 200)
        self.rum.refresh_from_db()
        self.assertEqual(self.rum.stock, Decimal("720.000"))

    def test_wastage_creates_transaction(self):
        from inventory.models import InventoryTransaction
        self._post(self.rum.id, {"quantity": "60", "reason": "Breakage", "notes": "Dropped bottle"})
        txn = InventoryTransaction.objects.get(item=self.rum, transaction_type="wastage")
        self.assertEqual(txn.quantity, Decimal("-60.000"))
        self.assertIn("Breakage", txn.reference)

    def test_wastage_returns_new_stock(self):
        resp = self._post(self.rum.id, {"quantity": "50", "reason": "Over-pour"})
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertAlmostEqual(data["new_stock"], 700.0, places=2)

    def test_wastage_rejects_zero_quantity(self):
        resp = self._post(self.rum.id, {"quantity": "0", "reason": "Spillage"})
        self.assertEqual(resp.status_code, 400)
        self.rum.refresh_from_db()
        self.assertEqual(self.rum.stock, Decimal("750.000"))

    def test_wastage_rejects_negative_quantity(self):
        resp = self._post(self.rum.id, {"quantity": "-10", "reason": "Spillage"})
        self.assertEqual(resp.status_code, 400)

    def test_wastage_rejects_excess_quantity(self):
        resp = self._post(self.rum.id, {"quantity": "999", "reason": "Spillage"})
        self.assertEqual(resp.status_code, 400)
        self.rum.refresh_from_db()
        self.assertEqual(self.rum.stock, Decimal("750.000"))

    def test_waiter_cannot_log_wastage(self):
        self.client.force_login(self.waiter)
        resp = self._post(self.rum.id, {"quantity": "30", "reason": "Spillage"})
        self.assertEqual(resp.status_code, 403)
        self.rum.refresh_from_db()
        self.assertEqual(self.rum.stock, Decimal("750.000"))

    def test_cross_tenant_item_not_accessible(self):
        other_tenant = Tenant.objects.create(name="Other Bar")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other")
        other_item = InventoryItem.objects.create(
            tenant=other_tenant, outlet=other_outlet,
            name="Vodka", unit="ml", stock=Decimal("500.000"),
        )
        resp = self._post(other_item.id, {"quantity": "30", "reason": "Spillage"})
        self.assertEqual(resp.status_code, 404)
        other_item.refresh_from_db()
        self.assertEqual(other_item.stock, Decimal("500.000"))


# ============================================================
# VARIANCE REPORT TESTS
# ============================================================


@_NO_MANIFEST
class VarianceReportTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Variance Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Bar")
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="varmanager", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet
        )
        self.rum = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Bacardi Rum", unit="ml",
            stock=Decimal("690.000"),
        )
        self.client.force_login(self.manager)

    def test_variance_report_loads(self):
        resp = self.client.get(reverse("inventory_variance"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Bacardi Rum")

    def test_variance_report_with_date_param(self):
        resp = self.client.get(reverse("inventory_variance") + "?date=2026-01-01")
        self.assertEqual(resp.status_code, 200)

    def test_variance_report_csv_export(self):
        resp = self.client.get(reverse("inventory_variance") + "?export=csv")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        content = resp.content.decode()
        self.assertIn("Bacardi Rum", content)
        self.assertIn("Variance %", content)

    def test_variance_zero_when_txn_matches_recipe(self):
        """Transactions consumed exactly what recipes expected — variance = 0."""
        from inventory.models import InventoryTransaction
        from menu.models import MenuItem, MenuCategory
        from inventory.models import Recipe as MenuRecipe
        from orders.models import Order, OrderItem

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Bar"
        )
        mojito = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Mojito", price=Decimal("300")
        )
        MenuRecipe.objects.create(
            menu_item=mojito, inventory_item=self.rum,
            quantity_required=Decimal("60.00"), unit="ml"
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            status="closed"
        )
        OrderItem.objects.create(
            order=order, menu_item=mojito, quantity=1,
            price=Decimal("300"), gst_percentage=Decimal("0"),
            total_price=Decimal("300"), status="served"
        )
        InventoryTransaction.objects.create(
            item=self.rum, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal("-60.000"), transaction_type="consume",
            reference="Order #1"
        )
        resp = self.client.get(reverse("inventory_variance"))
        rows = resp.context["rows"]
        rum_row = next(r for r in rows if r["item"].name == "Bacardi Rum")
        self.assertEqual(rum_row["recipe_expected"], Decimal("60.000"))
        self.assertEqual(rum_row["txn_consumed"], Decimal("60.000"))
        self.assertEqual(rum_row["variance"], Decimal("0.000"))

    def test_variance_negative_when_deduction_missed(self):
        """Recipe expected 60ml but no transaction fired — tracking gap (-60)."""
        from menu.models import MenuItem, MenuCategory
        from inventory.models import Recipe as MenuRecipe
        from orders.models import Order, OrderItem

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Bar2"
        )
        mojito = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Mojito2", price=Decimal("300")
        )
        MenuRecipe.objects.create(
            menu_item=mojito, inventory_item=self.rum,
            quantity_required=Decimal("60.00"), unit="ml"
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            status="closed"
        )
        OrderItem.objects.create(
            order=order, menu_item=mojito, quantity=1,
            price=Decimal("300"), gst_percentage=Decimal("0"),
            total_price=Decimal("300"), status="served"
        )
        # No InventoryTransaction — deduction never fired
        resp = self.client.get(reverse("inventory_variance"))
        rows = resp.context["rows"]
        rum_row = next(r for r in rows if r["item"].name == "Bacardi Rum")
        self.assertEqual(rum_row["recipe_expected"], Decimal("60.000"))
        self.assertEqual(rum_row["txn_consumed"], Decimal("0"))
        self.assertEqual(rum_row["variance"], Decimal("-60.000"))  # tracking gap

    def test_variance_positive_when_deduction_exceeds_orders(self):
        """Transaction shows 90ml consumed but recipes only expect 60ml — over-deduction (+30)."""
        from inventory.models import InventoryTransaction
        from menu.models import MenuItem, MenuCategory
        from inventory.models import Recipe as MenuRecipe
        from orders.models import Order, OrderItem

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Bar3"
        )
        mojito = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Mojito3", price=Decimal("300")
        )
        MenuRecipe.objects.create(
            menu_item=mojito, inventory_item=self.rum,
            quantity_required=Decimal("60.00"), unit="ml"
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            status="closed"
        )
        OrderItem.objects.create(
            order=order, menu_item=mojito, quantity=1,
            price=Decimal("300"), gst_percentage=Decimal("0"),
            total_price=Decimal("300"), status="served"
        )
        InventoryTransaction.objects.create(
            item=self.rum, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal("-90.000"), transaction_type="consume",
            reference="Order #1"
        )
        resp = self.client.get(reverse("inventory_variance"))
        rows = resp.context["rows"]
        rum_row = next(r for r in rows if r["item"].name == "Bacardi Rum")
        self.assertEqual(rum_row["recipe_expected"], Decimal("60.000"))
        self.assertEqual(rum_row["txn_consumed"], Decimal("90.000"))
        self.assertEqual(rum_row["variance"], Decimal("30.000"))


# ============================================================
# MODIFIER → INVENTORY DEDUCTION TESTS
# ============================================================

class ModifierInventoryTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Modifier Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Bar")
        User = get_user_model()
        self.user = User.objects.create_user(
            username="modtest", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet
        )
        self.rum = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Dark Rum", unit="ml", stock=Decimal("750.000"),
        )

    def _make_order_with_modifier(self, base_qty_required, modifier_qty_required):
        """Helper: creates MenuItem + Modifier with recipes, places order, returns order_items."""
        from inventory.models import Recipe, ModifierRecipe
        from menu.models import MenuItem, MenuCategory, ModifierGroup, Modifier
        from orders.models import Order, OrderItem, OrderItemModifier

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Cocktails"
        )
        item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Dark & Stormy", price=Decimal("350")
        )
        # Base recipe
        Recipe.objects.create(
            menu_item=item, inventory_item=self.rum,
            quantity_required=Decimal(str(base_qty_required)), unit="ml"
        )
        # Modifier
        group = ModifierGroup.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Extras"
        )
        mod = Modifier.objects.create(group=group, name="Extra Shot", price=Decimal("50"))
        # Modifier recipe
        ModifierRecipe.objects.create(
            modifier=mod, inventory_item=self.rum,
            quantity_required=Decimal(str(modifier_qty_required)), unit="ml"
        )
        # Order
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="open"
        )
        oi = OrderItem.objects.create(
            order=order, menu_item=item, quantity=1,
            price=Decimal("350"), gst_percentage=Decimal("0"),
            total_price=Decimal("350"), status="confirmed"
        )
        OrderItemModifier.objects.create(order_item=oi, modifier=mod, name=mod.name, price=mod.price)
        return list(OrderItem.objects.filter(order=order).select_related("menu_item"))

    def test_modifier_deducts_stock_on_kot(self):
        """Base recipe 60ml + Extra Shot modifier 60ml → 120ml total deducted."""
        from orders.services.inventory_service import deduct_inventory_for_items

        order_items = self._make_order_with_modifier(
            base_qty_required=60, modifier_qty_required=60
        )
        deduct_inventory_for_items(order_items)

        self.rum.refresh_from_db()
        self.assertEqual(self.rum.stock, Decimal("750.000") - Decimal("120.000"))

    def test_base_recipe_and_modifier_both_deduct_independently(self):
        """Verify base (45ml) and modifier (30ml) are each deducted correctly → 75ml."""
        from orders.services.inventory_service import deduct_inventory_for_items

        order_items = self._make_order_with_modifier(
            base_qty_required=45, modifier_qty_required=30
        )
        deduct_inventory_for_items(order_items)

        self.rum.refresh_from_db()
        self.assertEqual(self.rum.stock, Decimal("750.000") - Decimal("75.000"))

    def test_modifier_without_recipe_does_not_crash(self):
        """Modifier with no ModifierRecipe → deduction runs silently, only base recipe fires."""
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory, ModifierGroup, Modifier
        from orders.models import Order, OrderItem, OrderItemModifier
        from orders.services.inventory_service import deduct_inventory_for_items

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Food"
        )
        item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Burger", price=Decimal("200")
        )
        Recipe.objects.create(
            menu_item=item, inventory_item=self.rum,
            quantity_required=Decimal("30.00"), unit="ml"
        )
        group = ModifierGroup.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Sauces"
        )
        mod = Modifier.objects.create(group=group, name="Extra Ketchup", price=Decimal("0"))
        # No ModifierRecipe for this modifier

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="open"
        )
        oi = OrderItem.objects.create(
            order=order, menu_item=item, quantity=1,
            price=Decimal("200"), gst_percentage=Decimal("0"),
            total_price=Decimal("200"), status="confirmed"
        )
        OrderItemModifier.objects.create(order_item=oi, modifier=mod, name=mod.name, price=mod.price)

        order_items = list(OrderItem.objects.filter(order=order).select_related("menu_item"))
        deduct_inventory_for_items(order_items)  # must not raise

        self.rum.refresh_from_db()
        # Only base recipe deducted (30ml), modifier did nothing
        self.assertEqual(self.rum.stock, Decimal("750.000") - Decimal("30.000"))


# ============================================================
# VARIANCE REPORT — BUG FIX TESTS
# ============================================================

@_NO_MANIFEST
class VarianceReportBugFixTests(TestCase):
    """
    Covers the four bugs fixed in the variance report:
      Bug 1 — ModifierRecipe quantities were missing from recipe_expected
      Bug 2 — summary card counters used broken forloop.last logic
      Bug 3 — ok_count used variance_pct >= -3 (included high positive variance as OK)
      Bug 4 — positive variance was only amber; now treated equal to negative
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="VarFix Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Bar")
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="varfix_mgr", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet
        )
        self.rum = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Dark Rum", unit="ml",
            stock=Decimal("1000.000"),
        )
        self.client.force_login(self.manager)

    # ----------------------------------------------------------------
    # Bug 1: ModifierRecipe must be included in recipe_expected
    # ----------------------------------------------------------------

    def test_modifier_recipe_included_in_recipe_expected(self):
        """
        Base recipe 60ml + modifier recipe 30ml + transaction 90ml → variance = 0.
        Pre-fix: recipe_expected was 60, so variance showed a false +30.
        """
        from inventory.models import Recipe, ModifierRecipe, InventoryTransaction
        from menu.models import MenuItem, MenuCategory, ModifierGroup, Modifier
        from orders.models import Order, OrderItem, OrderItemModifier

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Cocktails"
        )
        mojito = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Mojito Special", price=Decimal("300")
        )
        Recipe.objects.create(
            menu_item=mojito, inventory_item=self.rum,
            quantity_required=Decimal("60.00"), unit="ml"
        )
        group = ModifierGroup.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Extras"
        )
        mod = Modifier.objects.create(group=group, name="Extra Shot", price=Decimal("50"))
        ModifierRecipe.objects.create(
            modifier=mod, inventory_item=self.rum,
            quantity_required=Decimal("30.00"), unit="ml"
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="closed"
        )
        oi = OrderItem.objects.create(
            order=order, menu_item=mojito, quantity=1,
            price=Decimal("300"), gst_percentage=Decimal("0"),
            total_price=Decimal("300"), status="served"
        )
        OrderItemModifier.objects.create(
            order_item=oi, modifier=mod, name=mod.name, price=mod.price
        )
        InventoryTransaction.objects.create(
            item=self.rum, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal("-90.000"), transaction_type="consume",
            reference="KOT #1"
        )

        resp = self.client.get(reverse("inventory_variance"))
        rows = resp.context["rows"]
        rum_row = next(r for r in rows if r["item"].name == "Dark Rum")

        self.assertEqual(rum_row["recipe_expected"], Decimal("90.00"))
        self.assertEqual(rum_row["txn_consumed"], Decimal("90.000"))
        self.assertEqual(rum_row["variance"], Decimal("0.00"))

    def test_modifier_without_recipe_does_not_inflate_expected(self):
        """
        Modifier with no ModifierRecipe — only the base recipe counts in expected.
        """
        from inventory.models import Recipe, InventoryTransaction
        from menu.models import MenuItem, MenuCategory, ModifierGroup, Modifier
        from orders.models import Order, OrderItem, OrderItemModifier

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Food"
        )
        burger = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Burger", price=Decimal("200")
        )
        Recipe.objects.create(
            menu_item=burger, inventory_item=self.rum,
            quantity_required=Decimal("30.00"), unit="ml"
        )
        group = ModifierGroup.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Sauces"
        )
        mod = Modifier.objects.create(group=group, name="Extra Ketchup", price=Decimal("0"))
        # No ModifierRecipe — modifier has no inventory link

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="closed"
        )
        oi = OrderItem.objects.create(
            order=order, menu_item=burger, quantity=1,
            price=Decimal("200"), gst_percentage=Decimal("0"),
            total_price=Decimal("200"), status="served"
        )
        OrderItemModifier.objects.create(
            order_item=oi, modifier=mod, name=mod.name, price=mod.price
        )
        InventoryTransaction.objects.create(
            item=self.rum, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal("-30.000"), transaction_type="consume",
            reference="KOT #1"
        )

        resp = self.client.get(reverse("inventory_variance"))
        rows = resp.context["rows"]
        rum_row = next(r for r in rows if r["item"].name == "Dark Rum")

        self.assertEqual(rum_row["recipe_expected"], Decimal("30.00"))
        self.assertEqual(rum_row["variance"], Decimal("0.00"))

    def test_two_modifiers_on_different_items_both_included(self):
        """
        mod1 → milk (+100ml), mod2 → rum (+30ml). Both added to recipe_expected.
        """
        from inventory.models import Recipe, ModifierRecipe, InventoryTransaction
        from menu.models import MenuItem, MenuCategory, ModifierGroup, Modifier
        from orders.models import Order, OrderItem, OrderItemModifier

        milk = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Milk", unit="ml", stock=Decimal("2000.000"),
        )
        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Coffee"
        )
        latte = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Latte", price=Decimal("150")
        )
        Recipe.objects.create(
            menu_item=latte, inventory_item=milk,
            quantity_required=Decimal("200.00"), unit="ml"
        )
        group = ModifierGroup.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Size"
        )
        mod1 = Modifier.objects.create(group=group, name="Extra Large", price=Decimal("20"))
        mod2 = Modifier.objects.create(group=group, name="Rum Shot", price=Decimal("50"))
        ModifierRecipe.objects.create(
            modifier=mod1, inventory_item=milk,
            quantity_required=Decimal("100.00"), unit="ml"
        )
        ModifierRecipe.objects.create(
            modifier=mod2, inventory_item=self.rum,
            quantity_required=Decimal("30.00"), unit="ml"
        )

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="closed"
        )
        oi = OrderItem.objects.create(
            order=order, menu_item=latte, quantity=1,
            price=Decimal("150"), gst_percentage=Decimal("0"),
            total_price=Decimal("150"), status="served"
        )
        OrderItemModifier.objects.create(order_item=oi, modifier=mod1, name=mod1.name, price=mod1.price)
        OrderItemModifier.objects.create(order_item=oi, modifier=mod2, name=mod2.name, price=mod2.price)

        InventoryTransaction.objects.create(
            item=milk, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal("-300.000"), transaction_type="consume", reference="KOT"
        )
        InventoryTransaction.objects.create(
            item=self.rum, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal("-30.000"), transaction_type="consume", reference="KOT"
        )

        resp = self.client.get(reverse("inventory_variance"))
        rows = resp.context["rows"]

        milk_row = next(r for r in rows if r["item"].name == "Milk")
        rum_row  = next(r for r in rows if r["item"].name == "Dark Rum")

        self.assertEqual(milk_row["recipe_expected"], Decimal("300.00"))
        self.assertEqual(milk_row["variance"], Decimal("0.00"))
        self.assertEqual(rum_row["recipe_expected"], Decimal("30.00"))
        self.assertEqual(rum_row["variance"], Decimal("0.00"))

    # ----------------------------------------------------------------
    # Bugs 2 & 3: Summary counts are correct and use abs(variance_pct)
    # ----------------------------------------------------------------

    def _make_item_with_variance(self, name, recipe_qty, txn_qty):
        """Create an item + closed order + transaction to produce a controlled variance."""
        from inventory.models import Recipe, InventoryTransaction
        from menu.models import MenuItem, MenuCategory
        from orders.models import Order, OrderItem

        item = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name=name, unit="ml", stock=Decimal("1000.000"),
        )
        cat, _ = MenuCategory.objects.get_or_create(
            tenant=self.tenant, outlet=self.outlet, name="Test Menu"
        )
        menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name=f"Dish {name}", price=Decimal("100")
        )
        Recipe.objects.create(
            menu_item=menu_item, inventory_item=item,
            quantity_required=Decimal(str(recipe_qty)), unit="ml"
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="closed"
        )
        OrderItem.objects.create(
            order=order, menu_item=menu_item, quantity=1,
            price=Decimal("100"), gst_percentage=Decimal("0"),
            total_price=Decimal("100"), status="served"
        )
        InventoryTransaction.objects.create(
            item=item, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal(str(-txn_qty)), transaction_type="consume",
            reference="test"
        )
        return item

    def test_summary_counts_correct(self):
        """ok/warn/critical counts match items at 1%, 6%, and 15% variance."""
        self._make_item_with_variance("OK Item",       recipe_qty=100, txn_qty=101)   # 1%  → ok
        self._make_item_with_variance("Warn Item",     recipe_qty=100, txn_qty=106)   # 6%  → warn
        self._make_item_with_variance("Critical Item", recipe_qty=100, txn_qty=115)   # 15% → critical

        resp = self.client.get(reverse("inventory_variance"))
        self.assertEqual(resp.context["ok_count"],       1)
        self.assertEqual(resp.context["warn_count"],     1)
        self.assertEqual(resp.context["critical_count"], 1)

    def test_positive_variance_flagged_same_as_negative(self):
        """
        +15% and -15% variance both count as critical.
        Pre-fix: negative >8% was red, positive >8% was only amber.
        """
        self._make_item_with_variance("Over Deduct",  recipe_qty=100, txn_qty=115)  # +15%
        self._make_item_with_variance("Under Deduct", recipe_qty=100, txn_qty=85)   # -15%

        resp = self.client.get(reverse("inventory_variance"))
        self.assertEqual(resp.context["critical_count"], 2)
        self.assertEqual(resp.context["ok_count"],       0)
        self.assertEqual(resp.context["warn_count"],     0)

    def test_ok_count_excludes_high_positive_variance(self):
        """
        Pre-fix ok_count used variance_pct >= -3 which included +50% as OK.
        Post-fix uses abs(variance_pct) <= 3 so +50% is critical, not OK.
        """
        self._make_item_with_variance("High Positive", recipe_qty=100, txn_qty=150)  # +50%

        resp = self.client.get(reverse("inventory_variance"))
        self.assertEqual(resp.context["ok_count"],       0)
        self.assertEqual(resp.context["critical_count"], 1)

    # ----------------------------------------------------------------
    # Existing behaviour still holds
    # ----------------------------------------------------------------

    def test_voided_items_excluded_from_recipe_expected(self):
        """Voided OrderItems must not contribute to recipe_expected."""
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from orders.models import Order, OrderItem

        cat = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains"
        )
        steak = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            category=cat, name="Steak", price=Decimal("800")
        )
        Recipe.objects.create(
            menu_item=steak, inventory_item=self.rum,
            quantity_required=Decimal("60.00"), unit="ml"
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="closed"
        )
        OrderItem.objects.create(
            order=order, menu_item=steak, quantity=1,
            price=Decimal("800"), gst_percentage=Decimal("0"),
            total_price=Decimal("800"), status="voided"
        )

        resp = self.client.get(reverse("inventory_variance"))
        rows = resp.context["rows"]
        rum_row = next(r for r in rows if r["item"].name == "Dark Rum")

        self.assertEqual(rum_row["recipe_expected"], Decimal("0"))
        self.assertEqual(rum_row["txn_consumed"],   Decimal("0"))
        self.assertEqual(rum_row["variance"],       Decimal("0"))