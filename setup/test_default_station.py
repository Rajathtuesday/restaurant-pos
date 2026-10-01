"""
The default kitchen station, and the errors that used to be swallowed.

An outlet has at most one default station (one_default_station_per_outlet).
get_default_station looked only for an ACTIVE default and otherwise created
one, so an outlet whose default had been switched off hit the constraint on
every call: the bill page failed, and the thermal print view hid it behind
`except Exception: pass`. Deleting the default station promoted the next
one while the old one was still the default, so the delete failed too.

Run: python manage.py test setup.test_default_station
"""
import json
from decimal import Decimal as D
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from printing.models import PrintJob
from setup.models import KitchenStation
from setup.services.station_service import get_default_station_for
from tenants.models import Outlet, SAMPLE_GSTIN, Tenant


class _World(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Station Cafe", slug="station-cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no=SAMPLE_GSTIN)

    def station(self, name, **fields):
        return KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name=name, **fields)


class DefaultStationLookupTest(_World):
    def test_the_active_default(self):
        self.station("Bar")
        kitchen = self.station("Kitchen", is_default=True)
        self.assertEqual(get_default_station_for(self.tenant, self.outlet), kitchen)

    def test_a_switched_off_default_hands_over_to_an_active_station(self):
        self.station("Kitchen", is_default=True, is_active=False)
        bar = self.station("Bar")
        self.assertEqual(get_default_station_for(self.tenant, self.outlet), bar)
        self.assertEqual(KitchenStation.objects.filter(outlet=self.outlet).count(), 2)

    def test_a_switched_off_default_with_nothing_active_is_still_used(self):
        kitchen = self.station("Kitchen", is_default=True, is_active=False)
        self.assertEqual(get_default_station_for(self.tenant, self.outlet), kitchen)

    def test_with_no_default_an_active_station_becomes_it(self):
        bar = self.station("Bar")
        self.assertEqual(get_default_station_for(self.tenant, self.outlet), bar)
        bar.refresh_from_db()
        self.assertTrue(bar.is_default)

    def test_with_no_station_a_general_default_is_made_once(self):
        first = get_default_station_for(self.tenant, self.outlet)
        second = get_default_station_for(self.tenant, self.outlet)
        self.assertEqual((first, first.name, first.is_default), (second, "General", True))


class PagesWithASwitchedOffDefaultTest(_World):
    def setUp(self):
        super().setUp()
        self.station("Kitchen", is_default=True, is_active=False)
        self.station("Bar")
        self.owner = User.objects.create_user(username="station_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.client.force_login(self.owner)
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                       name="Idli", price=D("40"), gst_percentage=D("5"))
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
        OrderItem.objects.create(order=self.order, menu_item=dish, quantity=2, price=dish.price,
                                 gst_percentage=dish.gst_percentage, total_price=dish.price * 2)
        self.order.recalculate_totals()

    def test_the_bill_page_opens(self):
        self.assertEqual(self.client.get(reverse("bill-view", args=[self.order.id])).status_code, 200)


class DeletingTheDefaultStationTest(_World):
    def test_the_next_station_takes_over_and_the_delete_goes_through(self):
        kitchen = self.station("Kitchen", is_default=True)
        bar = self.station("Bar")
        owner = User.objects.create_user(username="station_owner2", password="pw", role="owner",
                                         tenant=self.tenant, outlet=self.outlet)
        self.client.force_login(owner)
        resp = self.client.post(reverse("delete_station", args=[kitchen.id]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(json.loads(resp.content)["was_default"])
        self.assertFalse(KitchenStation.objects.filter(pk=kitchen.pk).exists())
        bar.refresh_from_db()
        self.assertTrue(bar.is_default)


class BestEffortFailuresAreLoggedTest(_World):
    def test_a_cache_failure_while_queuing_a_print_job_is_logged(self):
        with patch("django.core.cache.cache.set", side_effect=ConnectionError("redis down")):
            with self.assertLogs("pos.printing", level="WARNING") as logs:
                job = PrintJob.objects.create(tenant=self.tenant, outlet=self.outlet, payload={})
        self.assertTrue(PrintJob.objects.filter(pk=job.pk).exists())      # the job still saved
        self.assertIn(f"print-pending flag for outlet {self.outlet.id}", logs.output[0])
