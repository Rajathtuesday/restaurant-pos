"""
An order still open when the business day turns over can still go to the
kitchen (19 Nov 2026).

KOT numbers count from 1 again each business day (DailyKOTCounter), and an
order may not hold two KOTs with one number (KOTBatch: unique order +
kot_number). A pub order opened before the day's start hour and sent again
after it got today's number 1, which it already held from yesterday: the
database refused, and Send to Kitchen failed with "Could not send to
kitchen" for the rest of that order's life.
Run: python manage.py test kitchen.test_kot_day_rollover
"""
from decimal import Decimal

from django.test import Client, TestCase

from kitchen.models import DailyKOTCounter, KOTBatch
from menu.models import MenuItem
from orders.models import OrderItem
from core.tests.test_tenant_walk import Restaurant


class KotDayRolloverTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "kot-roll")
        # Today's counter has not started; the order already holds KOT 1
        # (the fixture's) from before the day turned over.
        DailyKOTCounter.objects.filter(tenant=self.r.tenant, outlet=self.r.outlet).delete()
        self.held = set(KOTBatch.objects.filter(order=self.r.order).values_list("kot_number", flat=True))
        self.assertIn(1, self.held)
        self.client = Client()
        self.client.force_login(self.r.owner)

    def add_pending(self, n=1):
        for i in range(n):
            dish = MenuItem.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, category=self.r.category,
                                           name=f"Late dish {i}", price=Decimal("100"))
            OrderItem.objects.create(order=self.r.order, menu_item=dish, quantity=1, price=dish.price,
                                     gst_percentage=Decimal("5"), total_price=dish.price, status="pending")

    def test_a_send_after_the_day_turns_over_gets_a_number_the_order_does_not_hold(self):
        self.add_pending()
        resp = self.client.post(f"/send-to-kitchen/{self.r.order.id}/")
        self.assertEqual(resp.status_code, 200, resp.content)
        numbers = list(KOTBatch.objects.filter(order=self.r.order).values_list("kot_number", flat=True))
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertNotIn(resp.json()["kots"][0], self.held)

    def test_the_skipped_number_is_used_up_so_numbers_still_rise_for_the_day(self):
        self.add_pending()
        first = self.client.post(f"/send-to-kitchen/{self.r.order.id}/").json()["kots"][0]
        self.add_pending()
        second = self.client.post(f"/send-to-kitchen/{self.r.order.id}/").json()["kots"][0]
        self.assertGreater(second, first)
