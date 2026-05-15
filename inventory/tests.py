#inventory/tests.py
from django.test import TestCase, Client
from decimal import Decimal
from django.urls import reverse
from django.contrib.auth import get_user_model

from tenants.models import Tenant, Outlet
from inventory.models import InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem

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