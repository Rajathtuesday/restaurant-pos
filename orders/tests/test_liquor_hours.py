"""
Liquor only in the licensed hours (20 Oct 2026).

A Karnataka CL-9 bar may serve 10 am to 1 am; police raided Bengaluru pubs
in 2025 for serving beyond permitted hours. Until now Rasova let a drink be
added to an order at any hour. Now a liquor line added outside the outlet's
hours (Outlet.liquor_from / liquor_until, 10:00 to 01:00 by default) is
refused, by staff and by a guest's QR order alike; food still sells, and
drinks ordered in time can still be billed and paid after 1 am.

The refusal tests fail on the code before this change.

Run: python manage.py test orders.tests.test_liquor_hours
"""
import json
from datetime import datetime, time
from decimal import Decimal as D
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from orders.exceptions import CartError
from orders.models import Order
from orders.services.order_service import add_items_to_order
from tenants.models import Outlet, Tenant, TenantFeatureOverride

IST = ZoneInfo("Asia/Kolkata")


def at(hour, minute=0):
    """Patch the clock to today at this time in Bengaluru."""
    return mock.patch("django.utils.timezone.now",
                      return_value=datetime(2026, 10, 24, hour, minute, tzinfo=IST))


class Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Hours Pub", tenant_type="pub")
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no="29ABCDE1234F1Z5")
        self.owner = User.objects.create_user(username="hours_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.pint = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=bar, name="Pint",
                                            price=D("300"), gst_percentage=D("0"),
                                            vat_class=add_liquor_class(self.outlet, "Beer"))
        self.fries = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food, name="Fries",
                                             price=D("150"), gst_percentage=D("5"))

    def order(self):
        return Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                    source="dine_in", status="open")

    def add(self, order, dish):
        add_items_to_order(self.owner, order, [{"id": dish.id, "quantity": 1}])


class LiquorHoursTest(Base):
    def test_a_drink_at_1_30_am_is_refused(self):
        with at(1, 30), self.assertRaises(CartError) as caught:
            self.add(self.order(), self.pint)
        self.assertEqual(str(caught.exception),
                         "'Pint' is liquor, and liquor can be ordered only from 10:00 am to 1:00 am.")

    def test_a_drink_at_9_am_is_refused_but_food_sells(self):
        order = self.order()
        with at(9, 0):
            with self.assertRaises(CartError):
                self.add(order, self.pint)
            self.add(order, self.fries)
        self.assertEqual(list(order.items.values_list("menu_item__name", flat=True)), ["Fries"])

    def test_a_drink_at_12_59_am_and_at_10_am_is_fine(self):
        order = self.order()
        with at(0, 59):
            self.add(order, self.pint)
        with at(10, 0):
            self.add(order, self.pint)
        self.assertEqual(order.items.count(), 2)

    def test_a_drink_ordered_in_time_is_still_paid_after_1_am(self):
        order = self.order()
        with at(0, 50):
            self.add(order, self.pint)
        with at(1, 20):
            order.refresh_from_db()
            order.status = "billing"
            order.save()
            order.recalculate_totals()
        order.refresh_from_db()
        self.assertEqual(order.grand_total, D("300.00"))

    def test_a_guests_qr_order_follows_the_same_hours(self):
        with at(2, 0), self.assertRaises(CartError):
            add_items_to_order(None, self.order(), [{"id": self.pint.id, "quantity": 1}],
                               tenant=self.tenant, outlet=self.outlet)

    def test_the_billing_screen_shows_why(self):
        with at(3, 0):
            client = Client()
            client.force_login(self.owner)   # under the same clock, or the session looks expired
            resp = client.post(reverse("create-order"), data=json.dumps({
                "source": "takeaway", "cart": [{"id": self.pint.id, "quantity": 1}]}),
                content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("liquor can be ordered only from 10:00 am to 1:00 am", resp.json()["error"])

    def test_the_owner_can_change_or_switch_off_the_hours(self):
        Outlet.objects.filter(id=self.outlet.id).update(liquor_until=time(2, 30))
        self.outlet.refresh_from_db()
        with at(2, 0):
            self.add(self.order(), self.pint)
        Outlet.objects.filter(id=self.outlet.id).update(liquor_hours_enforced=False)
        self.outlet.refresh_from_db()
        with at(5, 0):
            self.add(self.order(), self.pint)

    def test_the_settings_page_saves_the_hours(self):
        client = Client()
        client.force_login(self.owner)
        page = client.get(reverse("outlet_settings"))
        self.assertContains(page, 'name="liquor_from"')
        client.post(reverse("outlet_settings"), {
            "outlet_name": "Main", "gst_no": "29ABCDE1234F1Z5", "liquor_from": "11:00", "liquor_until": "23:30",
            "liquor_hours_enforced": "on"})
        self.outlet.refresh_from_db()
        self.assertEqual((self.outlet.liquor_from, self.outlet.liquor_until, self.outlet.liquor_hours_enforced),
                         (time(11, 0), time(23, 30), True))

    def test_hours_within_one_day(self):
        Outlet.objects.filter(id=self.outlet.id).update(liquor_from=time(11, 0), liquor_until=time(23, 0))
        self.outlet.refresh_from_db()
        self.assertEqual([self.outlet.liquor_allowed_at(datetime(2026, 10, 24, h, 0, tzinfo=IST)) for h in (10, 11, 22, 23)],
                         [False, True, True, False])
