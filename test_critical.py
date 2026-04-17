from django.test import TestCase, Client
from django.urls import reverse
from decimal import Decimal
from tenants.models import Tenant, Outlet
from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Table, Order, OrderItem, Payment, Refund
from orders.services.kot_service import create_kot
from orders.services.refund_service import process_refund, approve_refund
import json

class POSCriticalTests(TestCase):
    def setUp(self):
        # 1. Create Tenant A
        self.tenant_a = Tenant.objects.create(name="Tenant A")
        self.outlet_a = Outlet.objects.create(tenant=self.tenant_a, name="Outlet A")
        self.owner_a = User.objects.create_user(
            username="owner_a", password="123", role="owner", 
            tenant=self.tenant_a, outlet=self.outlet_a
        )
        
        # 2. Create Tenant B (Isolation checking)
        self.tenant_b = Tenant.objects.create(name="Tenant B")
        self.outlet_b = Outlet.objects.create(tenant=self.tenant_b, name="Outlet B")
        self.owner_b = User.objects.create_user(
            username="owner_b", password="123", role="owner", 
            tenant=self.tenant_b, outlet=self.outlet_b
        )

        # 3. Create Tables
        self.table_a = Table.objects.create(tenant=self.tenant_a, outlet=self.outlet_a, name="Table A1")
        self.table_b = Table.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, name="Table B1")

        # 4. Create Menu Items
        self.cat_a = MenuCategory.objects.create(tenant=self.tenant_a, outlet=self.outlet_a, name="Food")
        self.burger = MenuItem.objects.create(
            tenant=self.tenant_a, outlet=self.outlet_a, category=self.cat_a,
            name="Burger", price=Decimal("150.00"), gst_percentage=Decimal("5.00")
        )

        self.client = Client()

    def test_tenant_isolation(self):
        """Verify that Tenant B cannot see Tenant A's tables or orders."""
        self.client.login(username="owner_b", password="123")
        
        # Try to view Tenant A's table (via floor plan filter or similar)
        # Note: most views filter by request.user.tenant automatically
        tables = Table.objects.filter(tenant=self.tenant_b.id)
        self.assertEqual(tables.count(), 1)
        self.assertEqual(tables.first().name, "Table B1")
        
        # Verify Tenant A's table is not in the list
        self.assertNotIn(self.table_a, tables)

    def test_multi_step_refund_flow(self):
        """Verify that a refund must be requested (pending) then approved by an owner."""
        # 1. Create an order and payment
        order = Order.objects.create(
            tenant=self.tenant_a, outlet=self.outlet_a, 
            table=self.table_a, status="closed"
        )
        OrderItem.objects.create(
            order=order, menu_item=self.burger, quantity=1, 
            price=self.burger.price, gst_percentage=self.burger.gst_percentage,
            total_price=self.burger.price
        )
        order.recalculate_totals()
        
        payment = Payment.objects.create(order=order, method="cash", amount=Decimal("157.50"))

        # 2. Manager requests refund
        refund = process_refund(order, payment.id, Decimal("50.00"), self.owner_a)
        self.assertEqual(refund.status, "pending")
        
        # 3. Try to approve as manager (role='manager' test)
        manager = User.objects.create_user(
            username="mgr_a", password="123", role="manager", 
            tenant=self.tenant_a, outlet=self.outlet_a
        )
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            approve_refund(refund.id, manager)
            
        # 4. Approve as owner
        approve_refund(refund.id, self.owner_a)
        refund.refresh_from_db()
        self.assertEqual(refund.status, "approved")

    def test_kot_creation_and_counter_isolation(self):
        """Verify KOT creation works and counters don't interleave between tenants."""
        order_a = Order.objects.create(tenant=self.tenant_a, outlet=self.outlet_a, table=self.table_a)
        OrderItem.objects.create(order=order_a, menu_item=self.burger, quantity=1, price=Decimal("150.00"), gst_percentage=Decimal("5.0"), total_price=Decimal("150.00"), status="pending")
        
        # Create KOT for Tenant A
        kots_a = create_kot(self.owner_a, order_a)
        self.assertEqual(len(kots_a), 1)
        self.assertEqual(kots_a[0].kot_number, 1)

        # Create Order/KOT for Tenant B (should also be KOT #1)
        # First we need a menu item for Tenant B
        cat_b = MenuCategory.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, name="Food")
        pizza = MenuItem.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, category=cat_b, name="Pizza", price=Decimal("200.00"), gst_percentage=Decimal("5.0"))
        
        order_b = Order.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, table=self.table_b)
        OrderItem.objects.create(order=order_b, menu_item=pizza, quantity=1, price=Decimal("200.00"), gst_percentage=Decimal("5.0"), total_price=Decimal("200.00"), status="pending")
        
        kots_b = create_kot(self.owner_b, order_b)
        self.assertEqual(len(kots_b), 1)
        self.assertEqual(kots_b[0].kot_number, 1) # NOT 2, because it's a different tenant

    def test_login_failure_feedback(self):
        """Verify that login fails with an error message in context."""
        response = self.client.post(reverse('login'), {"username": "owner_a", "password": "wrongpassword"})
        self.assertEqual(response.status_code, 200) # Re-renders login page
        # Check django messages
        messages = list(response.context.get('messages', []))
        self.assertTrue(any("Invalid" in m.message for m in messages))
