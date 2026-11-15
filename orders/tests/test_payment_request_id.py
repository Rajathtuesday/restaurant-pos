"""
A cash payment sent twice is recorded once (Payment.request_key).

Why (15 Nov 2026): a cash payment taken offline, or one whose answer was
lost when the connection dropped, is kept on the device and sent again.
If the server had taken the first one:
- a payment that closed the order came back "Order already completed", and
  the cashier was told the payment "could not be synced" although it went
  through;
- a part payment was simply recorded a second time: the cash counted twice.
The screen now gives each payment an id and sends the same id with every
copy; the server answers a repeat with the first payment.
Run: python manage.py test orders.tests.test_payment_request_id
"""
import json
from decimal import Decimal

from django.test import Client, TestCase

from core.tests.test_tenant_walk import Restaurant
from orders.models import Order, Payment
from shifts.models import CashSession

KEY = "3f1c2b6e-8d4a-4c1e-9b7a-1a2b3c4d5e6f"


class PaymentRequestIdTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "pay-key")
        CashSession.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, status="open",
                                   opened_by=self.r.owner, opening_balance=Decimal("0"))
        self.order = self.r.order
        self.order.recalculate_totals()
        self.order.refresh_from_db()
        # The fixture already holds a payment (it has a refund to test).
        self.before = set(Payment.objects.filter(order=self.order).values_list("id", flat=True))
        paid = sum((p.amount for p in Payment.objects.filter(order=self.order).exclude(method="refund")), Decimal("0"))
        self.total = self.order.grand_total - paid          # what is left to pay
        self.client = Client()
        self.client.force_login(self.r.owner)

    def pay(self, amount, key=KEY, order=None):
        body = {"method": "cash", "amount": str(amount)}
        if key is not None:
            body["request_id"] = key
        return self.client.post(f"/pay/{(order or self.order).id}/", data=json.dumps(body),
                                content_type="application/json")

    def cash(self):
        return list(Payment.objects.filter(order=self.order).exclude(id__in=self.before)
                    .values_list("amount", flat=True))

    def test_a_payment_that_closed_the_order_is_answered_again_not_refused(self):
        first = self.pay(self.total)
        self.assertEqual(first.status_code, 200, first.content)
        again = self.pay(self.total)
        self.assertEqual(again.status_code, 200, again.content)
        self.assertTrue(again.json()["repeat"])
        self.assertEqual(self.cash(), [self.total])

    def test_a_part_payment_sent_twice_is_counted_once(self):
        self.pay(Decimal("100"))
        again = self.pay(Decimal("100"))
        self.assertEqual(again.status_code, 200, again.content)
        self.assertTrue(again.json()["repeat"])
        self.assertEqual(again.json()["remaining"], float(self.total - Decimal("100")))
        self.assertEqual(self.cash(), [Decimal("100.00")])

    def test_two_payments_with_their_own_ids_are_two_payments(self):
        self.pay(Decimal("100"), key="a" * 8)
        self.pay(Decimal("50"), key="b" * 8)
        self.assertEqual(sorted(self.cash()), [Decimal("50.00"), Decimal("100.00")])

    def test_without_an_id_nothing_changes(self):
        self.pay(Decimal("100"), key=None)
        self.pay(Decimal("50"), key=None)
        self.assertEqual(len(self.cash()), 2)

    def test_an_id_that_is_not_one_is_refused(self):
        resp = self.pay(Decimal("100"), key="<script>")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.cash(), [])

    def test_the_same_id_on_another_order_is_its_own_payment(self):
        other = Order.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, table=self.r.table2,
                                     created_by=self.r.owner, status="open", source="dine_in")
        from orders.models import OrderItem
        OrderItem.objects.create(order=other, menu_item=self.r.menu_item, quantity=1, price=self.r.menu_item.price,
                                 gst_percentage=self.r.menu_item.gst_percentage, total_price=self.r.menu_item.price)
        other.recalculate_totals()
        self.pay(Decimal("100"))
        resp = self.pay(Decimal("100"), order=other)
        self.assertNotIn("repeat", resp.json())
        self.assertEqual(Payment.objects.filter(order=other).count(), 1)
