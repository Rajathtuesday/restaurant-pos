"""
Two people settling one bill at the same moment (19 Nov 2026).

Checked because a review listed "concurrent checkout / split-bill races" as
open. They are not: pay_order and process_payment lock the order row, and
recalculate_totals locks it again. These tests hold that in place with real
threads and real connections.
Run: python manage.py test orders.tests.test_checkout_race
"""
import json
from decimal import Decimal
from threading import Barrier, Thread

from django.db import connection
from django.test import Client, TransactionTestCase

from core.tests.test_tenant_walk import Restaurant
from orders.models import Order, Payment
from shifts.models import CashSession


class CheckoutRaceTest(TransactionTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "pay-race")
        CashSession.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, status="open",
                                   opened_by=self.r.owner, opening_balance=Decimal("0"))
        self.order = self.r.order
        self.order.recalculate_totals()
        self.order.refresh_from_db()
        self.before = set(Payment.objects.filter(order=self.order).values_list("id", flat=True))
        paid = sum((p.amount for p in Payment.objects.filter(order=self.order).exclude(method="refund")), Decimal("0"))
        self.left = self.order.grand_total - paid

    def at_once(self, bodies):
        results = [None] * len(bodies)
        start = Barrier(len(bodies))

        def one(i):
            try:
                client = Client()
                client.force_login(self.r.owner)
                start.wait(timeout=10)
                results[i] = client.post(f"/pay/{self.order.id}/", data=json.dumps(bodies[i]),
                                         content_type="application/json")
            except Exception as e:  # noqa: BLE001 -- recorded for the assertion
                results[i] = f"ERROR: {e}"
            finally:
                connection.close()

        threads = [Thread(target=one, args=(i,)) for i in range(len(bodies))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual([x for x in results if isinstance(x, str)], [])
        return results

    def new_payments(self):
        return list(Payment.objects.filter(order=self.order).exclude(id__in=self.before).values_list("amount", flat=True))

    def test_two_cashiers_paying_the_whole_bill_take_it_once(self):
        body = {"method": "cash", "amount": str(self.left)}
        results = self.at_once([body, dict(body)])
        self.assertEqual(sorted(x.status_code for x in results), [200, 400], [x.content for x in results])
        self.assertEqual(self.new_payments(), [self.left])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "closed")

    def test_split_shares_paid_at_once_never_take_more_than_the_bill(self):
        share = (self.left / 2).quantize(Decimal("0.01"))
        bodies = [{"method": "cash", "amount": str(share)} for _ in range(3)]   # one share too many
        self.at_once(bodies)
        self.assertLessEqual(sum(self.new_payments()), self.left)
        self.assertEqual(sum(self.new_payments()), self.left)
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, "closed")
