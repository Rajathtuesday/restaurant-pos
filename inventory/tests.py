#inventory/tests.py
import json
from django.test import TestCase, Client, override_settings
from decimal import Decimal
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone

from tenants.models import Tenant, Outlet
from inventory.models import (
    InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem,
    StockRequisition, RequisitionItem,
)


def _variance_report_url():
    """
    inventory_variance defaults to get_business_date(now) when no ?date=
    is given, which treats anything before 6 AM local time as still
    yesterday's business day. Orders created via auto_now_add in these
    tests stamp the real calendar date, so passing that date explicitly
    bypasses the cutoff — these tests shouldn't pass or fail depending on
    what time of day they happen to run.

    Must use localtime, not a bare UTC date: Order.created_at__date is
    evaluated by Django against TIME_ZONE (Asia/Kolkata), so the date
    that actually matches a just-created order is the local calendar
    date, not whatever date() a raw UTC `now()` happens to land on.
    """
    local_today = timezone.localtime(timezone.now()).date()
    return reverse("inventory_variance") + f"?date={local_today.isoformat()}"

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
        resp = self.client.get(_variance_report_url())
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
        resp = self.client.get(_variance_report_url())
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
        resp = self.client.get(_variance_report_url())
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
        resp = self.client.get(_variance_report_url())
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

        resp = self.client.get(_variance_report_url())
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

        resp = self.client.get(_variance_report_url())
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

        resp = self.client.get(_variance_report_url())
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

        resp = self.client.get(_variance_report_url())
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

        resp = self.client.get(_variance_report_url())
        self.assertEqual(resp.context["critical_count"], 2)
        self.assertEqual(resp.context["ok_count"],       0)
        self.assertEqual(resp.context["warn_count"],     0)

    def test_ok_count_excludes_high_positive_variance(self):
        """
        Pre-fix ok_count used variance_pct >= -3 which included +50% as OK.
        Post-fix uses abs(variance_pct) <= 3 so +50% is critical, not OK.
        """
        self._make_item_with_variance("High Positive", recipe_qty=100, txn_qty=150)  # +50%

        resp = self.client.get(_variance_report_url())
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

        resp = self.client.get(_variance_report_url())
        rows = resp.context["rows"]
        rum_row = next(r for r in rows if r["item"].name == "Dark Rum")

        self.assertEqual(rum_row["recipe_expected"], Decimal("0"))
        self.assertEqual(rum_row["txn_consumed"],   Decimal("0"))
        self.assertEqual(rum_row["variance"],       Decimal("0"))


def _consumption_report_url():
    """Same business-date reasoning as _variance_report_url() — consumption_report
    also defaults to get_business_date(now) when no ?date= is given."""
    local_today = timezone.localtime(timezone.now()).date()
    return reverse("inventory_consumption") + f"?date={local_today.isoformat()}"


class VarianceReportRecipeUnitConversionTests(TestCase):
    """recipe_map (the 'expected consumption' side of the variance report) must
    convert a recipe's unit into the inventory item's own unit before summing
    — the exact same conversion deduct_inventory_for_items already applies at
    KOT time. Before this fix, recipe_map used the raw recipe quantity, so a
    recipe in grams against a kg-tracked item made recipe_expected wildly
    different from txn_consumed (which WAS correctly converted), showing a
    fake variance for a perfectly healthy item."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Variance Unit Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="varunit_mgr", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet,
        )
        # Tracked in KG — recipe will be written in grams.
        self.flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Flour", unit="kg", stock=Decimal("10.000"),
        )
        self.client.force_login(self.manager)

    def _sell_naan(self, recipe_qty, recipe_unit, order_qty=2):
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from orders.models import Order, OrderItem

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Naan", price=Decimal("60"),
        )
        Recipe.objects.create(
            menu_item=naan, inventory_item=self.flour,
            quantity_required=Decimal(str(recipe_qty)), unit=recipe_unit,
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="closed")
        OrderItem.objects.create(
            order=order, menu_item=naan, quantity=order_qty,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="served",
        )
        return naan

    def test_recipe_in_grams_against_kg_item_shows_zero_variance_when_healthy(self):
        """Recipe: 500g flour per Naan (0.5kg), item tracked in kg. 2 Naans sold
        → expected 1.0kg. If the real KOT deduction also converted correctly
        (1.0kg consumed), variance must be 0 — not a false +999kg from
        comparing an unconverted 1000g against a converted 1.0kg."""
        from inventory.models import InventoryTransaction

        self._sell_naan(recipe_qty=500, recipe_unit="g", order_qty=2)
        InventoryTransaction.objects.create(
            item=self.flour, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal("-1.000"), transaction_type="consume", reference="KOT #1",
        )

        resp = self.client.get(_variance_report_url())
        rows = resp.context["rows"]
        flour_row = next(r for r in rows if r["item"].name == "Flour")

        self.assertEqual(flour_row["recipe_expected"], Decimal("1.000"))
        self.assertEqual(flour_row["txn_consumed"], Decimal("1.000"))
        self.assertEqual(flour_row["variance"], Decimal("0.000"))

    def test_incompatible_recipe_unit_is_skipped_not_counted(self):
        """Recipe accidentally set to 'pcs' against a kg-tracked item — must be
        excluded from recipe_expected entirely, never treated as a raw number."""
        self._sell_naan(recipe_qty=2, recipe_unit="pcs", order_qty=3)

        resp = self.client.get(_variance_report_url())
        rows = resp.context["rows"]
        flour_row = next(r for r in rows if r["item"].name == "Flour")

        self.assertEqual(flour_row["recipe_expected"], Decimal("0"))

    def test_modifier_recipe_unit_conversion_in_recipe_expected(self):
        """Same conversion requirement for ModifierRecipe as for Recipe."""
        from inventory.models import ModifierRecipe, InventoryTransaction
        from menu.models import MenuItem, MenuCategory, ModifierGroup, Modifier
        from orders.models import Order, OrderItem, OrderItemModifier

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Naan", price=Decimal("60"),
        )
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        mod = Modifier.objects.create(group=group, name="Extra Flour Dusting", price=Decimal("10"))
        ModifierRecipe.objects.create(
            modifier=mod, inventory_item=self.flour,
            quantity_required=Decimal("200"), unit="g",  # 200g, item tracked in kg
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="closed")
        oi = OrderItem.objects.create(
            order=order, menu_item=naan, quantity=1,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="served",
        )
        OrderItemModifier.objects.create(order_item=oi, modifier=mod, name=mod.name, price=mod.price)
        InventoryTransaction.objects.create(
            item=self.flour, tenant=self.tenant, outlet=self.outlet,
            quantity=Decimal("-0.200"), transaction_type="consume", reference="KOT #1",
        )

        resp = self.client.get(_variance_report_url())
        rows = resp.context["rows"]
        flour_row = next(r for r in rows if r["item"].name == "Flour")

        self.assertEqual(flour_row["recipe_expected"], Decimal("0.200"))
        self.assertEqual(flour_row["variance"], Decimal("0.000"))


class ConsumptionReportBugFixTests(TestCase):
    """consumption_report had two real bugs: (1) it used recipe.quantity_required
    raw, without converting to the inventory item's unit, same class of bug as
    variance_report above; (2) it only ever looked at base Recipe links,
    silently ignoring anything consumed through a modifier (e.g. Extra Cheese)."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Consumption Unit Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="consunit_mgr", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet,
        )
        self.flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Flour", unit="kg", stock=Decimal("10.000"),
        )
        self.client.force_login(self.manager)

    def test_recipe_quantity_converted_to_item_unit(self):
        """500g flour per Naan, item tracked in kg, 4 sold → consumed must be
        2.000 (kg), not 2000 (treating grams as if they were already kg)."""
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from orders.models import Order, OrderItem

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Naan", price=Decimal("60"),
        )
        Recipe.objects.create(
            menu_item=naan, inventory_item=self.flour,
            quantity_required=Decimal("500"), unit="g",
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="closed")
        OrderItem.objects.create(
            order=order, menu_item=naan, quantity=4,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="served",
        )

        resp = self.client.get(_consumption_report_url())
        rows = resp.context["report_rows"]
        flour_row = next(r for r in rows if r["item"].name == "Flour")

        self.assertEqual(flour_row["consumed"], Decimal("2.000"))

    def test_incompatible_recipe_unit_is_skipped_not_shown_as_garbage(self):
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from orders.models import Order, OrderItem

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Naan", price=Decimal("60"),
        )
        Recipe.objects.create(
            menu_item=naan, inventory_item=self.flour,
            quantity_required=Decimal("2"), unit="pcs",  # incompatible with kg
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="closed")
        OrderItem.objects.create(
            order=order, menu_item=naan, quantity=1,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="served",
        )

        resp = self.client.get(_consumption_report_url())
        rows = resp.context["report_rows"]
        self.assertFalse(any(r["item"].name == "Flour" for r in rows))

    def test_modifier_consumption_now_included(self):
        """Previously: modifier-linked inventory consumption was invisible on
        this report entirely — only base recipes were summed."""
        from inventory.models import ModifierRecipe
        from menu.models import MenuItem, MenuCategory, ModifierGroup, Modifier
        from orders.models import Order, OrderItem, OrderItemModifier

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Naan", price=Decimal("60"),
        )
        # No base Recipe at all — only a modifier link.
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        mod = Modifier.objects.create(group=group, name="Extra Flour Dusting", price=Decimal("10"))
        ModifierRecipe.objects.create(
            modifier=mod, inventory_item=self.flour,
            quantity_required=Decimal("100"), unit="g",
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="closed")
        oi = OrderItem.objects.create(
            order=order, menu_item=naan, quantity=3,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="served",
        )
        OrderItemModifier.objects.create(order_item=oi, modifier=mod, name=mod.name, price=mod.price)

        resp = self.client.get(_consumption_report_url())
        rows = resp.context["report_rows"]
        flour_row = next((r for r in rows if r["item"].name == "Flour"), None)

        self.assertIsNotNone(flour_row, "Modifier-driven consumption must appear in the report")
        self.assertEqual(flour_row["consumed"], Decimal("0.300"))  # 100g × 3, in kg

    def test_usage_bar_width_reflects_consumed_over_total(self):
        """The Usage column's bar width used to be set to raw remaining stock
        used directly as a CSS percentage (e.g. 'width:500%'), not an actual
        consumed/total ratio. 50 consumed + 50 remaining should render 50%."""
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from orders.models import Order, OrderItem

        self.flour.stock = Decimal("50.000")
        self.flour.save(update_fields=["stock"])
        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Naan", price=Decimal("60"),
        )
        Recipe.objects.create(
            menu_item=naan, inventory_item=self.flour,
            quantity_required=Decimal("50"), unit="kg",
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="closed")
        OrderItem.objects.create(
            order=order, menu_item=naan, quantity=1,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="served",
        )

        resp = self.client.get(_consumption_report_url())
        self.assertContains(resp, "width:50%")


class InventoryItemCategoryTests(TestCase):
    """Free-text category field — lightweight grouping/filtering aid, no
    separate model. Covers persistence, clearing, and truncation."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Category Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="cat_mgr", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet,
        )
        self.client.force_login(self.manager)

    def test_create_with_category(self):
        resp = self.client.post(
            reverse("create_inventory_item"),
            data=json.dumps({"name": "Cabbage", "unit": "g", "category": "Vegetables"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        item = InventoryItem.objects.get(id=resp.json()["id"])
        self.assertEqual(item.category, "Vegetables")

    def test_create_without_category_defaults_blank(self):
        resp = self.client.post(
            reverse("create_inventory_item"),
            data=json.dumps({"name": "Salt", "unit": "g"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        item = InventoryItem.objects.get(id=resp.json()["id"])
        self.assertEqual(item.category, "")

    def test_update_sets_category(self):
        item = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Onion", unit="g",
        )
        resp = self.client.post(
            reverse("update_inventory_item", args=[item.id]),
            data=json.dumps({"category": "Vegetables"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.category, "Vegetables")

    def test_update_can_clear_category(self):
        item = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Onion", unit="g", category="Vegetables",
        )
        resp = self.client.post(
            reverse("update_inventory_item", args=[item.id]),
            data=json.dumps({"category": ""}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.category, "")

    def test_category_is_truncated_and_stripped(self):
        resp = self.client.post(
            reverse("create_inventory_item"),
            data=json.dumps({"name": "Ginger", "unit": "g", "category": "  " + "x" * 150}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        item = InventoryItem.objects.get(id=resp.json()["id"])
        self.assertEqual(len(item.category), 100)
        self.assertFalse(item.category.startswith(" "))


# ---------------------------------------------------------------------------
# UNIT CONVERSION
#
# Recipe/ModifierRecipe/BatchItem each store their own `unit`, independent of
# the InventoryItem they point at. A recipe entered in grams against an item
# tracked in kilograms used to be treated as a raw number — off by 1000x.
# ---------------------------------------------------------------------------

class UnitConversionUtilTests(TestCase):
    """Pure tests of inventory.unit_conversion — no DB involved."""

    def test_grams_to_kilograms(self):
        from inventory.unit_conversion import convert_quantity
        self.assertEqual(convert_quantity(Decimal("500"), "g", "kg"), Decimal("0.5"))

    def test_kilograms_to_grams(self):
        from inventory.unit_conversion import convert_quantity
        self.assertEqual(convert_quantity(Decimal("2"), "kg", "g"), Decimal("2000"))

    def test_millilitres_to_litres(self):
        from inventory.unit_conversion import convert_quantity
        self.assertEqual(convert_quantity(Decimal("250"), "ml", "l"), Decimal("0.25"))

    def test_same_unit_is_a_no_op(self):
        from inventory.unit_conversion import convert_quantity
        self.assertEqual(convert_quantity(Decimal("42"), "kg", "kg"), Decimal("42"))

    def test_weight_to_volume_is_incompatible(self):
        from inventory.unit_conversion import convert_quantity, IncompatibleUnitsError
        with self.assertRaises(IncompatibleUnitsError):
            convert_quantity(Decimal("100"), "g", "ml")

    def test_pieces_to_weight_is_incompatible(self):
        from inventory.unit_conversion import convert_quantity, IncompatibleUnitsError
        with self.assertRaises(IncompatibleUnitsError):
            convert_quantity(Decimal("3"), "pcs", "kg")

    def test_units_compatible_true_within_family(self):
        from inventory.unit_conversion import units_compatible
        self.assertTrue(units_compatible("g", "kg"))
        self.assertTrue(units_compatible("ml", "l"))
        self.assertTrue(units_compatible("pcs", "pcs"))

    def test_units_compatible_false_across_families(self):
        from inventory.unit_conversion import units_compatible
        self.assertFalse(units_compatible("g", "pcs"))
        self.assertFalse(units_compatible("l", "kg"))


class RecipeDeductionUnitConversionTests(TestCase):
    """deduct_inventory_for_items (the real KOT-time deduction path) must
    convert a recipe's unit into the inventory item's own unit before
    touching stock, and must fail safe — skip and log, never guess — when
    the two units can't be converted at all."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Unit Conv Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        # Tracked in KILOGRAMS.
        self.flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Flour", unit="kg", stock=Decimal("10.000"),
        )

    def _order_item_with_recipe(self, recipe_qty, recipe_unit, order_qty=1):
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from orders.models import Order, OrderItem

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat,
            name="Naan", price=Decimal("60"),
        )
        Recipe.objects.create(
            menu_item=item, inventory_item=self.flour,
            quantity_required=Decimal(str(recipe_qty)), unit=recipe_unit,
        )
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
        oi = OrderItem.objects.create(
            order=order, menu_item=item, quantity=order_qty,
            price=Decimal("60"), gst_percentage=Decimal("0"),
            total_price=Decimal("60"), status="confirmed",
        )
        return list(OrderItem.objects.filter(order=order).select_related("menu_item"))

    def test_recipe_in_grams_deducts_correctly_from_kg_tracked_item(self):
        """Recipe: 500g flour per Naan. Item tracked in kg. Must deduct 0.5kg, not 500kg."""
        from orders.services.inventory_service import deduct_inventory_for_items

        order_items = self._order_item_with_recipe(recipe_qty=500, recipe_unit="g")
        deduct_inventory_for_items(order_items)

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000") - Decimal("0.5"))

    def test_recipe_in_same_unit_as_item_still_works(self):
        from orders.services.inventory_service import deduct_inventory_for_items

        order_items = self._order_item_with_recipe(recipe_qty=Decimal("1.5"), recipe_unit="kg")
        deduct_inventory_for_items(order_items)

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000") - Decimal("1.5"))

    def test_incompatible_unit_recipe_is_skipped_not_corrupted(self):
        """Recipe accidentally set to 'pcs' against a kg-tracked item — must
        NOT deduct a meaningless number. Stock stays untouched."""
        from orders.services.inventory_service import deduct_inventory_for_items

        order_items = self._order_item_with_recipe(recipe_qty=2, recipe_unit="pcs")
        deduct_inventory_for_items(order_items)  # must not raise

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))  # unchanged


class AddRecipeUnitDefaultTests(TestCase):
    """add_recipe (menu/views/item_views.py) previously had no unit field at
    all and every recipe silently got the model's hardcoded unit='g' default
    — wrong for anything not tracked in grams. Now defaults to the inventory
    item's own unit, and rejects an explicitly incompatible one."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Recipe Unit Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        User = get_user_model()
        self.user = User.objects.create_user(
            username="recipe_owner", password="pwd",
            role="owner", tenant=self.tenant, outlet=self.outlet,
        )
        from menu.models import MenuItem, MenuCategory
        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat,
            name="Chicken Curry", price=Decimal("250"),
        )
        # Tracked in PIECES, not weight — the case that used to be silently
        # broken (recipe defaulted to unit="g" regardless).
        self.eggs = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Eggs", unit="pcs", stock=Decimal("100"),
        )

    def test_recipe_without_unit_defaults_to_inventory_items_unit(self):
        from inventory.models import Recipe
        client = Client()
        client.force_login(self.user)
        resp = client.post(
            reverse("add_recipe"),
            data={"menu_item": self.menu_item.id, "inventory_item": self.eggs.id, "quantity": "2"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        recipe = Recipe.objects.get(menu_item=self.menu_item, inventory_item=self.eggs)
        self.assertEqual(recipe.unit, "pcs")  # NOT the old hardcoded "g" default

    def test_recipe_with_incompatible_unit_is_rejected(self):
        from inventory.models import Recipe
        client = Client()
        client.force_login(self.user)
        resp = client.post(
            reverse("add_recipe"),
            data={
                "menu_item": self.menu_item.id, "inventory_item": self.eggs.id,
                "quantity": "2", "unit": "kg",  # eggs are tracked in pcs, not weight
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Recipe.objects.filter(menu_item=self.menu_item, inventory_item=self.eggs).exists())

    def test_updating_quantity_only_does_not_reset_a_custom_unit(self):
        """A recipe deliberately entered in grams against a kg-tracked item
        must keep that unit when only the quantity is later updated."""
        from inventory.models import Recipe
        flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Flour2", unit="kg", stock=Decimal("5"),
        )
        Recipe.objects.create(
            menu_item=self.menu_item, inventory_item=flour,
            quantity_required=Decimal("500"), unit="g",
        )
        client = Client()
        client.force_login(self.user)
        resp = client.post(
            reverse("add_recipe"),
            data={"menu_item": self.menu_item.id, "inventory_item": flour.id, "quantity": "750"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        recipe = Recipe.objects.get(menu_item=self.menu_item, inventory_item=flour)
        self.assertEqual(recipe.unit, "g")  # unchanged
        self.assertEqual(recipe.quantity_required, Decimal("750"))


class GrossMarginCogsUnitConversionTests(TestCase):
    """gross_margin_report's COGS calculation multiplies recipe quantity by
    the inventory item's cost_price (Rs per inventory-item unit) — the
    quantity must be converted into that unit first."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="COGS Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        User = get_user_model()
        self.user = User.objects.create_user(
            username="cogs_owner", password="pwd",
            role="owner", tenant=self.tenant, outlet=self.outlet,
        )
        # Costed at Rs 100 per KILOGRAM.
        self.sugar = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Sugar", unit="kg", stock=Decimal("50"), cost_price=Decimal("100.00"),
        )

    def test_cogs_converts_gram_recipe_against_kg_costed_item(self):
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from orders.models import Order, OrderItem
        from reports.services.pl_reports import gross_margin_report

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Desserts")
        item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat,
            name="Gulab Jamun", price=Decimal("120"),
        )
        # 200g of sugar per plate — item is costed per KG.
        Recipe.objects.create(
            menu_item=item, inventory_item=self.sugar,
            quantity_required=Decimal("200"), unit="g",
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="closed",
            subtotal=Decimal("120"), gst_total=Decimal("0"), grand_total=Decimal("120"),
        )
        OrderItem.objects.create(
            order=order, menu_item=item, quantity=1,
            price=Decimal("120"), gst_percentage=Decimal("0"),
            total_price=Decimal("120"), status="served",
        )

        today = timezone.localdate()
        result = gross_margin_report(self.tenant, self.outlet, today, today)

        # 200g = 0.2kg * Rs 100/kg = Rs 20 COGS. The old bug would have
        # computed 200 * 100 = Rs 20,000 (1000x too high).
        self.assertEqual(result["cogs"], 20.0)

    def test_modifier_recipe_included_in_cogs(self):
        """COGS previously only summed base Recipe links — a paid modifier's
        inventory link (e.g. Extra Cheese) was silently excluded, undercosting
        (and overstating margin on) any dish sold with it."""
        from inventory.models import ModifierRecipe
        from menu.models import MenuItem, MenuCategory, ModifierGroup, Modifier
        from orders.models import Order, OrderItem, OrderItemModifier
        from reports.services.pl_reports import gross_margin_report

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Desserts")
        item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat,
            name="Plain Toast", price=Decimal("50"),
        )
        # No base recipe at all — only a modifier link, same as a real
        # "Extra X" add-on with no base ingredient cost of its own.
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        mod = Modifier.objects.create(group=group, name="Extra Sugar", price=Decimal("5"))
        ModifierRecipe.objects.create(
            modifier=mod, inventory_item=self.sugar,
            quantity_required=Decimal("200"), unit="g",  # item costed per kg
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="closed",
            subtotal=Decimal("55"), gst_total=Decimal("0"), grand_total=Decimal("55"),
        )
        oi = OrderItem.objects.create(
            order=order, menu_item=item, quantity=1,
            price=Decimal("50"), gst_percentage=Decimal("0"),
            total_price=Decimal("55"), status="served",
        )
        OrderItemModifier.objects.create(order_item=oi, modifier=mod, name=mod.name, price=mod.price)

        today = timezone.localdate()
        result = gross_margin_report(self.tenant, self.outlet, today, today)

        # 200g sugar = 0.2kg * Rs 100/kg = Rs 20 — previously Rs 0 (excluded).
        self.assertEqual(result["cogs"], 20.0)
        self.assertEqual(result["items_with_recipe"], 1)


class ProductionCapacityUnitConversionTests(TestCase):
    """production_capacity() (reports/services/inventory_reports.py) divides
    stock by recipe.quantity_required to say how many portions can be made —
    must convert into the inventory item's own unit first, same bug class as
    the consumption/variance reports and COGS."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="ProdCap Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            name="Flour", unit="kg", stock=Decimal("10.000"),
        )

    def test_capacity_converts_gram_recipe_against_kg_item(self):
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from reports.services.inventory_reports import production_capacity

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Naan", price=Decimal("60"),
        )
        # 500g per Naan, item tracked in kg — 10kg stock should support 20 Naans,
        # not 10/500 = 0 (the pre-fix bug, treating grams as if already kg).
        Recipe.objects.create(
            menu_item=naan, inventory_item=self.flour,
            quantity_required=Decimal("500"), unit="g",
        )

        results = production_capacity(self.tenant, self.outlet)
        naan_row = next(r for r in results if r["menu_item"] == "Naan")

        self.assertEqual(naan_row["max_portions"], 20)
        self.assertEqual(naan_row["bottleneck"], "Flour")

    def test_incompatible_unit_treated_as_zero_capacity(self):
        """A recipe with a genuinely incompatible unit is a data problem, not
        a stock shortage — but must still show as 0, not silently excluded,
        since the dish's real deduction is unreliable either way."""
        from inventory.models import Recipe
        from menu.models import MenuItem, MenuCategory
        from reports.services.inventory_reports import production_capacity

        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=cat, name="Naan", price=Decimal("60"),
        )
        Recipe.objects.create(
            menu_item=naan, inventory_item=self.flour,
            quantity_required=Decimal("2"), unit="pcs",  # incompatible with kg
        )

        results = production_capacity(self.tenant, self.outlet)
        naan_row = next(r for r in results if r["menu_item"] == "Naan")

        self.assertEqual(naan_row["max_portions"], 0)


class ConvertToPOTests(TestCase):
    """
    Coverage for inventory/requisition_views.py:convert_to_po.

    This view had zero test coverage before this class. Its own code once
    used field/relation names that don't exist on these models and raised
    TypeError on every call — it had genuinely never run. These tests also
    cover the follow-up bug found once that was fixed: converting a
    requisition for a supplier that already has an auto-generated draft PO
    (from InventoryItem.trigger_reorder) used to raise IntegrityError,
    because PurchaseOrder only allows one draft per (tenant, outlet,
    supplier) and the old code did a plain .create() instead of reusing
    the existing draft.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="PO Convert Tenant", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

        User = get_user_model()
        self.manager = User.objects.create_user(
            username="po_convert_mgr", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet,
        )
        self.waiter = User.objects.create_user(
            username="po_convert_waiter", password="pwd",
            role="waiter", tenant=self.tenant, outlet=self.outlet,
        )

        self.supplier_a = Supplier.objects.create(tenant=self.tenant, outlet=self.outlet, name="Vendor A")
        self.supplier_b = Supplier.objects.create(tenant=self.tenant, outlet=self.outlet, name="Vendor B")

        self.flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Flour", unit="kg",
            stock=Decimal("5.000"), cost_price=Decimal("40.00"),
            preferred_supplier=self.supplier_a,
        )
        self.sugar = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Sugar", unit="kg",
            stock=Decimal("5.000"), cost_price=Decimal("50.00"),
            preferred_supplier=self.supplier_b,
        )
        self.salt = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Salt", unit="kg",
            stock=Decimal("5.000"), cost_price=Decimal("20.00"),
            preferred_supplier=None,  # deliberately no preferred supplier
        )

        self.client.force_login(self.manager)

    def _make_requisition(self, items):
        """items: list of (inventory_item, quantity_requested)."""
        req = StockRequisition.objects.create(
            tenant=self.tenant, requesting_outlet=self.outlet,
            status="approved", route="external",
            created_by=self.manager, approved_by=self.manager,
        )
        for inv_item, qty in items:
            RequisitionItem.objects.create(
                requisition=req, inventory_item=inv_item,
                quantity_requested=Decimal(str(qty)), unit=inv_item.unit,
            )
        return req

    def _to_po(self, req):
        return self.client.post(reverse("requisition-to-po", args=[req.id]))

    def test_creates_new_draft_po_when_none_exists(self):
        req = self._make_requisition([(self.flour, "10")])
        resp = self._to_po(req)
        data = resp.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["pos_created"], 1)
        self.assertEqual(data["pos_reused"], 0)
        self.assertEqual(PurchaseOrder.objects.count(), 1)

        po = PurchaseOrder.objects.first()
        self.assertEqual(po.supplier, self.supplier_a)
        self.assertEqual(po.status, "draft")
        self.assertTrue(po.po_number)
        self.assertEqual(po.items.get(item=self.flour).quantity, Decimal("10.000"))

        req.refresh_from_db()
        self.assertEqual(req.status, "ordered")
        self.assertEqual(req.purchase_order, po)

    def test_splits_into_one_po_per_supplier(self):
        req = self._make_requisition([(self.flour, "10"), (self.sugar, "5")])
        resp = self._to_po(req)
        data = resp.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["pos_created"], 2)
        self.assertEqual(PurchaseOrder.objects.count(), 2)
        suppliers = set(PurchaseOrder.objects.values_list("supplier_id", flat=True))
        self.assertEqual(suppliers, {self.supplier_a.id, self.supplier_b.id})

    def test_reuses_existing_draft_instead_of_crashing(self):
        """The core regression test: a draft PO for supplier_a already
        exists (as trigger_reorder would create), simulating the auto
        low-stock path having already fired. Converting a requisition for
        the same supplier must not raise IntegrityError — it should merge
        into that existing draft."""
        existing_po = PurchaseOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, supplier=self.supplier_a,
            status="draft", notes="Auto-generated due to low stock",
        )
        PurchaseOrderItem.objects.create(
            purchase_order=existing_po, item=self.flour,
            quantity=Decimal("3.000"), unit_price=Decimal("40.00"),
        )

        req = self._make_requisition([(self.flour, "10")])
        resp = self._to_po(req)
        data = resp.json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["pos_created"], 0)
        self.assertEqual(data["pos_reused"], 1)

        # Still exactly one draft PO for this supplier — reused, not duplicated.
        self.assertEqual(
            PurchaseOrder.objects.filter(supplier=self.supplier_a, status="draft").count(), 1
        )
        existing_po.refresh_from_db()
        # Same item already on the PO — quantities merged, not duplicated as a second line.
        self.assertEqual(existing_po.items.count(), 1)
        self.assertEqual(existing_po.items.get(item=self.flour).quantity, Decimal("13.000"))

        req.refresh_from_db()
        self.assertEqual(req.status, "ordered")
        self.assertEqual(req.purchase_order_id, existing_po.id)

    def test_items_with_no_preferred_supplier_are_named_not_just_counted(self):
        req = self._make_requisition([(self.flour, "10"), (self.salt, "2")])
        resp = self._to_po(req)
        data = resp.json()

        self.assertTrue(data["success"])
        self.assertEqual(data["skipped"], 1)
        self.assertEqual(data["skipped_items"], ["Salt"])
        self.assertIn("Salt", data["message"])

    def test_all_items_missing_supplier_returns_clear_error(self):
        req = self._make_requisition([(self.salt, "2")])
        resp = self._to_po(req)
        data = resp.json()

        self.assertEqual(resp.status_code, 400)
        self.assertIn("preferred supplier", data["error"])
        self.assertEqual(data["skipped_items"], ["Salt"])
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_draft_requisition_cannot_be_converted(self):
        req = self._make_requisition([(self.flour, "10")])
        req.status = "draft"
        req.save(update_fields=["status"])

        resp = self._to_po(req)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_waiter_role_is_forbidden(self):
        self.client.force_login(self.waiter)
        req = self._make_requisition([(self.flour, "10")])
        resp = self._to_po(req)
        self.assertEqual(resp.status_code, 403)

    def test_po_numbers_do_not_collide_across_two_conversions(self):
        req1 = self._make_requisition([(self.flour, "10")])
        req2 = self._make_requisition([(self.sugar, "5")])

        self._to_po(req1)
        self._to_po(req2)

        numbers = list(PurchaseOrder.objects.values_list("po_number", flat=True))
        self.assertEqual(len(numbers), len(set(numbers)), "PO numbers must be unique")

    def test_po_number_format_matches_auto_reorder_path(self):
        """Both PO-creation paths (this view and InventoryItem.trigger_reorder)
        must share the same generate_po_number helper, so numbers look the
        same regardless of which path created them."""
        self.flour.reorder_quantity = Decimal("5.000")
        self.flour.low_stock_threshold = Decimal("10.000")
        self.flour.save()
        self.flour.trigger_reorder()
        auto_po = PurchaseOrder.objects.get(supplier=self.supplier_a)

        req = self._make_requisition([(self.sugar, "5")])
        self._to_po(req)
        manual_po = PurchaseOrder.objects.get(supplier=self.supplier_b)

        import re
        pattern = r"^PO-\d+-\d{4}-\d{4}$"
        self.assertRegex(auto_po.po_number, pattern)
        self.assertRegex(manual_po.po_number, pattern)


class AutoRouteTests(TestCase):
    """
    Coverage for StockRequisition.auto_route(), previously untested.

    Outlet.is_central_kitchen didn't exist before this — auto_route() used
    to guess which outlet was the central kitchen purely from
    ProductionBatch history (whichever outlet made the first-ever batch),
    with no explicit flag anywhere on Outlet. That heuristic is kept as a
    fallback for tenants that haven't set the flag yet, but the flag wins
    whenever it's set.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Route Tenant", tenant_type="franchise")
        self.branch = Outlet.objects.create(tenant=self.tenant, name="Branch")
        self.ck = Outlet.objects.create(tenant=self.tenant, name="Central Kitchen")

        self.flour = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.branch, name="Flour", unit="kg",
            stock=Decimal("0.000"),
        )

    def _make_req(self, qty="5"):
        req = StockRequisition.objects.create(
            tenant=self.tenant, requesting_outlet=self.branch, status="pending",
        )
        RequisitionItem.objects.create(
            requisition=req, inventory_item=self.flour,
            quantity_requested=Decimal(qty), unit="kg",
        )
        return req

    def test_uses_explicit_flag_when_set(self):
        self.ck.is_central_kitchen = True
        self.ck.save()
        # CK stock exists under the same item name at the CK outlet.
        InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.ck, name="Flour", unit="kg",
            stock=Decimal("50.000"),
        )

        req = self._make_req(qty="5")
        req.auto_route()

        self.assertEqual(req.route, "internal")
        self.assertEqual(req.fulfilling_outlet, self.ck)

    def test_falls_back_to_batch_heuristic_when_no_flag_set(self):
        """No outlet has is_central_kitchen=True anywhere for this tenant —
        must fall back to the old 'first outlet to produce a batch' guess,
        so tenants that existed before this field shipped aren't broken."""
        from inventory.models import ProductionBatch

        ProductionBatch.objects.create(
            tenant=self.tenant, batch_number="BATCH-0001", source_outlet=self.ck,
        )
        InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.ck, name="Flour", unit="kg",
            stock=Decimal("50.000"),
        )

        req = self._make_req(qty="5")
        req.auto_route()

        self.assertEqual(req.route, "internal")
        self.assertEqual(req.fulfilling_outlet, self.ck)

    def test_goes_external_when_ck_stock_insufficient(self):
        self.ck.is_central_kitchen = True
        self.ck.save()
        InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.ck, name="Flour", unit="kg",
            stock=Decimal("1.000"),  # less than the 5 requested
        )

        req = self._make_req(qty="5")
        req.auto_route()

        self.assertEqual(req.route, "external")
        self.assertIsNone(req.fulfilling_outlet)

    def test_goes_external_when_no_central_kitchen_at_all(self):
        req = self._make_req(qty="5")
        req.auto_route()

        self.assertEqual(req.route, "external")
        self.assertIsNone(req.fulfilling_outlet)

    def test_requesting_outlet_flagged_as_own_ck_goes_external(self):
        """A requisition raised BY the central kitchen itself can't be
        fulfilled by itself — must go to a vendor, not loop internally."""
        self.branch.is_central_kitchen = True
        self.branch.save()

        req = self._make_req(qty="5")
        req.auto_route()

        self.assertEqual(req.route, "external")