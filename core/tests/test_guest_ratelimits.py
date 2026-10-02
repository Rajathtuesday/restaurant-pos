"""
Guests on one restaurant's Wi-Fi don't share one rate-limit allowance
(core/ratelimit_keys.py).

Before: every public QR page was limited per IP, so all the guests behind a
restaurant's router counted as one. Three phones following their orders
(each checks about 10 times a minute) used up order_status's 30 a minute
between them; two tables ordering at once shared create_order's 20; two
cashiers on the shop Wi-Fi shared it too. Now a guest is counted by IP and
the token they came with, staff by login, and each page keeps a looser cap
per IP.

Run: python manage.py test core.tests.test_guest_ratelimits
"""
import json
import uuid
from decimal import Decimal as D

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from core.testing import freeze_ratelimit_clock
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from orders.views.public_views import make_order_status_token
from tenants.models import Outlet, Tenant


@override_settings(RATELIMIT_ENABLE=True)
class SharedWifiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        freeze_ratelimit_clock(self)
        self.tenant = Tenant.objects.create(name="Shared Wifi Cafe", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                            name="Dosa", price=D("80"))
        self.t1 = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.t2 = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T2")

    def order(self, token, **extra):
        return self.client.post(reverse("create-order"), content_type="application/json", data=json.dumps(
            {"table_token": str(token), "cart": [{"id": self.dish.id, "quantity": 1}], **extra}))

    def test_two_tables_each_open_the_menu_30_times(self):
        for table in (self.t1, self.t2):
            for _ in range(30):
                self.assertEqual(self.client.get(reverse("menu_view", args=[table.qr_token])).status_code, 200)
        self.assertEqual(self.client.get(reverse("menu_view", args=[self.t1.qr_token])).status_code, 429)

    def test_three_phones_follow_their_orders(self):
        t3 = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T3")
        tokens = [make_order_status_token(Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=table, status="open").id)
            for table in (self.t1, self.t2, t3)]
        for _ in range(12):             # a little over a minute of checking every 6 s, each
            for token in tokens:
                self.assertEqual(self.client.get(reverse("order_status", args=[token])).status_code, 200)

    def test_two_tables_each_place_20_orders(self):
        for table in (self.t1, self.t2):
            for _ in range(20):
                self.assertEqual(self.order(table.qr_token).status_code, 200)
        self.assertEqual(self.order(self.t1.qr_token).status_code, 429)

    def test_two_cashiers_on_the_shop_wifi_are_counted_apart(self):
        for n in range(2):
            cashier = User.objects.create_user(username=f"wifi_cashier_{n}", password="pw", role="cashier",
                                               tenant=self.tenant, outlet=self.outlet)
            self.client.force_login(cashier)
            for _ in range(25):         # more than a table's 20
                resp = self.client.post(reverse("create-order"), content_type="application/json", data=json.dumps(
                    {"cart": [{"id": self.dish.id, "quantity": 1}], "source": "takeaway"}))
                self.assertEqual(resp.status_code, 200, resp.content)

    def test_made_up_tokens_still_hit_the_cap_per_ip(self):
        for _ in range(120):
            self.assertNotEqual(self.order(uuid.uuid4()).status_code, 429)
        self.assertEqual(self.order(uuid.uuid4()).status_code, 429)
        # ...and it applies to real tables from that IP too
        self.assertEqual(self.order(self.t2.qr_token).status_code, 429)
