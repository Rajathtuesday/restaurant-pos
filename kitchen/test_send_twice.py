"""
Send to Kitchen tapped twice at the same moment makes one KOT (19 Nov 2026).

The order row is locked, so two sends run one after the other and only the
first finds dishes to send. The second used to fail with "Could not send to
kitchen. Please try again." (create_kot raised a bare Exception for "nothing
pending"), which told a waiter in a rush to tap a third time. Both now
answer success; the second says there was nothing new to send.
Real threads, real connections.
Run: python manage.py test kitchen.test_send_twice
"""
from threading import Barrier, Thread

from django.db import connection
from django.test import Client, TransactionTestCase

from core.tests.test_tenant_walk import Restaurant
from core.utils import get_business_date
from django.utils import timezone
from kitchen.models import DailyKOTCounter, KOTBatch
from orders.models import OrderItem


class SendToKitchenTwiceTest(TransactionTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "kot-twice")
        OrderItem.objects.filter(order=self.r.order).update(status="pending")
        # The fixture's order already holds a KOT; today's counter starts past it.
        DailyKOTCounter.objects.update_or_create(
            tenant=self.r.tenant, outlet=self.r.outlet, date=get_business_date(timezone.now(), self.r.outlet),
            defaults={"value": 50})
        self.before = KOTBatch.objects.filter(order=self.r.order).count()
        self.url = f"/send-to-kitchen/{self.r.order.id}/"

    def send(self):
        client = Client()
        client.force_login(self.r.owner)
        return client.post(self.url)

    def test_two_at_once_make_one_kot_and_both_say_ok(self):
        n = 4
        results = [None] * n
        start = Barrier(n)

        def one(i):
            try:
                client = Client()
                client.force_login(self.r.owner)
                start.wait(timeout=10)
                results[i] = client.post(self.url)
            except Exception as e:  # noqa: BLE001 -- recorded for the assertion
                results[i] = f"ERROR: {e}"
            finally:
                connection.close()

        threads = [Thread(target=one, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual([r for r in results if isinstance(r, str)], [])
        self.assertEqual([r.status_code for r in results], [200] * n, [r.content for r in results])
        self.assertEqual(KOTBatch.objects.filter(order=self.r.order).count() - self.before, 1, "a dish reached the kitchen twice")
        made = [r.json() for r in results if r.json()["kots"]]
        self.assertEqual(len(made), 1)

    def test_a_second_send_with_nothing_new_says_so(self):
        self.assertEqual(self.send().status_code, 200)
        again = self.send()
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json()["kots"], [])
        self.assertIn("Nothing new", again.json()["message"])
