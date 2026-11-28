"""
What the service worker may keep for offline, as the server marks it
(core/offline_copy.py): a dish's options are kept (billing adds a dish with
options while the connection is down); the live data (kitchen tickets, the
floor, an order, today's numbers, a receipt) is never marked, so the worker
never hands an old copy back as if live (static/js/tests/sw.test.mjs).

Why (28 Nov 2026): the worker kept every script answer like a page, and a
failed read got the old copy (or the billing page) back.

Run: python manage.py test core.tests.test_offline_copy
"""
from django.test import Client, TestCase

from accounts.models import User
from core import offline_copy
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tenants.models import Outlet, Tenant, TenantFeatureOverride


class OfflineMarksTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Offline Marks Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        for feature in ("modifiers", "kitchen_display", "floor_plan"):
            TenantFeatureOverride.objects.update_or_create(tenant=self.tenant, feature=feature, defaults={"enabled": True})
        self.owner = User.objects.create_user(username="om_owner", password="pw", tenant=self.tenant,
                                              outlet=self.outlet, role="owner")
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Coffee")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                            name="Latte", price=180)
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, table=table, status="open")
        self.browser = Client()
        self.browser.force_login(self.owner)

    def test_a_dishs_options_may_be_kept_for_offline_billing(self):
        response = self.browser.get(f"/menu/item-modifiers/{self.dish.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response[offline_copy.HEADER], offline_copy.KEEP)

    def test_live_data_is_never_marked_keep(self):
        for url in ("/kitchen-data/", "/tables-data/", f"/order-data/{self.order.id}/", "/live-orders/data/",
                    "/dashboard/metrics.json"):
            with self.subTest(url=url):
                response = self.browser.get(url, HTTP_ACCEPT="application/json")
                self.assertEqual(response.status_code, 200, url)
                self.assertNotEqual(response.get(offline_copy.HEADER), offline_copy.KEEP, url)
