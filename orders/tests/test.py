from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from tenants.models import Tenant, Outlet
from orders.models import Order, Table

User = get_user_model()

class OrderLockTestCase(TestCase):
    def setUp(self):
        # Create test data
        self.tenant = Tenant.objects.create(name="Test", slug="test")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Outlet")
        self.user1 = User.objects.create_user(
            username='user1',
            password='pass123',
            tenant=self.tenant,
            outlet=self.outlet
        )
        self.user2 = User.objects.create_user(
            username='user2',
            password='pass123',
            tenant=self.tenant,
            outlet=self.outlet
        )
        self.table = Table.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Table 1"
        )
        self.order = Order.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            table=self.table,
            created_by=self.user1
        )
        self.client = Client()
    
    def test_order_lock_fetched_before_check(self):
        """Test that order is fetched before lock is checked"""
        self.client.login(username='user1', password='pass123')
        
        response = self.client.get(f'/orders/{self.order.id}/billing/')
        
        # Should succeed (user1 created it)
        self.assertEqual(response.status_code, 200)
    
    def test_order_lock_prevents_other_user(self):
        """Test that other user cannot edit locked order"""
        # User1 locks the order
        self.order.locked_by = self.user1
        self.order.save()
        
        # User2 tries to access
        self.client.login(username='user2', password='pass123')
        response = self.client.get(f'/orders/{self.order.id}/billing/')
        
        # Should be denied
        self.assertIn("locked", response.content.decode().lower())