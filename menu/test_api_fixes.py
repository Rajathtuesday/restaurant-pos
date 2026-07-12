"""
Regression tests for the menu API field-name fixes (C7).

Both endpoints previously referenced fields that don't exist
(MenuCategory 'order' instead of 'display_order'; MenuItem
'dietary_preference'/'spice_level' which were never defined), so they
500'd on every call. They must now return 200 with real data.

Run: python manage.py test menu.test_api_fixes
"""
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from tenants.models import Tenant, Outlet


class MenuApiTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Menu API Tenant", slug="menu-api")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.user = User.objects.create_user(
            username="mgr", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains", display_order=1
        )
        MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Dal", price=Decimal("120.00"), is_veg=True, is_available=True,
        )

    def test_api_categories_ok(self):
        client = Client()
        client.force_login(self.user)
        resp = client.get(reverse("api_categories"))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"][0]["name"], "Mains")

    def test_api_items_ok(self):
        client = Client()
        client.force_login(self.user)
        resp = client.get(reverse("api_items"))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        items = body["data"][0]["items"]
        self.assertEqual(items[0]["name"], "Dal")
        self.assertIn("is_veg", items[0])
