#inventory/tests.py
from django.test import TestCase
from decimal import Decimal

from tenants.models import Tenant, Outlet
from inventory.models import InventoryItem


class InventoryTests(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Test Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main"
        )

        self.item = InventoryItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Cheese",
            unit="kg",
            stock=Decimal("10"),
            low_stock_threshold=Decimal("2")
        )


    def test_reduce_stock(self):

        self.item.reduce_stock(Decimal("3"))

        self.item.refresh_from_db()

        self.assertEqual(self.item.stock, Decimal("7"))


    def test_add_stock(self):

        self.item.add_stock(Decimal("5"))

        self.item.refresh_from_db()

        self.assertEqual(self.item.stock, Decimal("15"))


    def test_low_stock_flag(self):

        self.item.reduce_stock(Decimal("9"))

        self.item.refresh_from_db()

        self.assertTrue(self.item.is_low_stock)