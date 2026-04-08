import pytest
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from accounts.models import User
from tenants.models import Tenant, Outlet
from orders.models import Order, OrderItem, KOTBatch, Table
from menu.models import MenuItem, Category
from reports.services.kitchen_reports import kitchen_performance, top_kitchen_items

class KitchenReportsTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant", domain="test_com")
        self.outlet_1 = Outlet.objects.create(tenant=self.tenant, name="Branch 1")
        self.outlet_2 = Outlet.objects.create(tenant=self.tenant, name="Branch 2")
        
        self.table_1 = Table.objects.create(tenant=self.tenant, outlet=self.outlet_1, name="T1")
        self.table_2 = Table.objects.create(tenant=self.tenant, outlet=self.outlet_2, name="T2")

        self.category = Category.objects.create(tenant=self.tenant, name="Main Course")
        self.menu_item_1 = MenuItem.objects.create(category=self.category, name="Burger", price=100)
        self.menu_item_2 = MenuItem.objects.create(category=self.category, name="Pizza", price=200)

        # Order 1 in Outlet 1
        self.order_1 = Order.objects.create(tenant=self.tenant, outlet=self.outlet_1, table=self.table_1)
        self.kot_1 = KOTBatch.objects.create(tenant=self.tenant, outlet=self.outlet_1, order=self.order_1, kot_number=1)
        self.kot_2 = KOTBatch.objects.create(tenant=self.tenant, outlet=self.outlet_1, order=self.order_1, kot_number=2)

        # 2 Burgers (Not voided)
        OrderItem.objects.create(
            order=self.order_1, menu_item=self.menu_item_1, quantity=2, price=100, gst_percentage=0, total_price=200, kot=self.kot_1
        )
        # 1 Pizza (Voided)
        OrderItem.objects.create(
            order=self.order_1, menu_item=self.menu_item_2, quantity=1, price=200, gst_percentage=0, total_price=200, kot=self.kot_2, status="voided"
        )
        # 1 Burger (Not voided)
        OrderItem.objects.create(
            order=self.order_1, menu_item=self.menu_item_1, quantity=1, price=100, gst_percentage=0, total_price=100, kot=self.kot_2
        )

        # Order 2 in Outlet 2
        self.order_2 = Order.objects.create(tenant=self.tenant, outlet=self.outlet_2, table=self.table_2)
        self.kot_3 = KOTBatch.objects.create(tenant=self.tenant, outlet=self.outlet_2, order=self.order_2, kot_number=1)
        
        # 3 Pizzas (Not voided)
        OrderItem.objects.create(
            order=self.order_2, menu_item=self.menu_item_2, quantity=3, price=200, gst_percentage=0, total_price=600, kot=self.kot_3
        )

    def test_kitchen_performance_all_outlets(self):
        # We should have 3 KOTs, 6 items prepared (2 burgers+1 pizza voided+1 burger+3 pizzas), 1 total_voided
        perf = kitchen_performance(self.tenant, start_date=timezone.now().date(), end_date=timezone.now().date())
        self.assertEqual(perf["total_kots"], 3)
        self.assertEqual(perf["total_items_prepared"], 7)
        self.assertEqual(perf["total_voided"], 1)

    def test_kitchen_performance_specific_outlet(self):
        # Outlet 1: 2 KOTs, 2+1+1=4 items, 1 voided
        perf = kitchen_performance(self.tenant, outlet=self.outlet_1, start_date=timezone.now().date(), end_date=timezone.now().date())
        self.assertEqual(perf["total_kots"], 2)
        self.assertEqual(perf["total_items_prepared"], 4)
        self.assertEqual(perf["total_voided"], 1)

        # Outlet 2: 1 KOT, 3 items, 0 voided
        perf_2 = kitchen_performance(self.tenant, outlet=self.outlet_2, start_date=timezone.now().date(), end_date=timezone.now().date())
        self.assertEqual(perf_2["total_kots"], 1)
        self.assertEqual(perf_2["total_items_prepared"], 3)
        self.assertEqual(perf_2["total_voided"], 0)

    def test_top_kitchen_items(self):
        top_items = top_kitchen_items(self.tenant, start_date=timezone.now().date(), end_date=timezone.now().date())
        # The voided pizza should NOT be counted in top_items
        # Total Burger = 3 (from Outlet 1)
        # Total Pizza = 3 (from Outlet 2) (1 voided in Outlet 1 is ignored)
        # It's an unordered dictionary comparison since both have 3
        items_dict = {item["menu_item__name"]: item["total_qty"] for item in top_items}
        self.assertEqual(items_dict.get("Burger"), 3)
        self.assertEqual(items_dict.get("Pizza"), 3)

    def test_top_kitchen_items_specific_outlet(self):
        top_items_1 = top_kitchen_items(self.tenant, outlet=self.outlet_1, start_date=timezone.now().date(), end_date=timezone.now().date())
        items_dict_1 = {item["menu_item__name"]: item["total_qty"] for item in top_items_1}
        self.assertEqual(items_dict_1.get("Burger"), 3)
        self.assertIsNone(items_dict_1.get("Pizza"))

    def test_edge_case_no_orders(self):
        past_date = timezone.now().date() - timedelta(days=10)
        perf = kitchen_performance(self.tenant, start_date=past_date, end_date=past_date)
        
        self.assertEqual(perf["total_kots"], 0)
        self.assertEqual(perf["total_items_prepared"], 0)
        self.assertEqual(perf["total_voided"], 0)
        
        top_items = top_kitchen_items(self.tenant, start_date=past_date, end_date=past_date)
        self.assertEqual(len(top_items), 0)

