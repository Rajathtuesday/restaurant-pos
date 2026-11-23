"""
How a printed bill says it was paid (orders/services/bill_layout.py
payment_rows), found on the walkthrough recording, 23 Nov 2026: a bill
split between two friends printed after the first one paid by UPI, and the
paper said "Paid via UPI" against the whole total with half still owed. The
bill page now prints only once it is settled (static/js/bill/payment.js),
and the paper says what really happened.

Run: python manage.py test orders.tests.test_bill_payment_rows
"""
from decimal import Decimal
from unittest import mock

from orders.models import Payment
from orders.services.bill_layout import payment_rows
from orders.tests.test_kot_prints_once import PRINTED, CapturedNetwork, KotTestBase
from printing.services.printing_service import PrintingService


class PaymentRowsTests(KotTestBase):

    def setUp(self):
        super().setUp()
        self.old_style_line("")
        self.order.recalculate_totals()
        self.order.refresh_from_db()
        self.total = self.order.grand_total

    def pay(self, method, amount):
        Payment.objects.create(order=self.order, method=method, amount=Decimal(amount), created_by=self.user)

    def test_one_method_for_the_whole_bill(self):
        self.pay("upi", self.total)
        self.assertEqual(payment_rows(self.order), (("Paid via", "UPI"),))

    def test_half_paid_says_what_is_still_due(self):
        half = (self.total / 2).quantize(Decimal("0.01"))
        self.pay("upi", half)
        self.assertEqual(payment_rows(self.order),
                         (("Paid via", "UPI"), ("Balance due", f"{self.total - half:.2f}")))

    def test_a_split_lists_each_method_with_its_amount(self):
        half = (self.total / 2).quantize(Decimal("0.01"))
        self.pay("upi", half)
        self.pay("cash", self.total - half)
        self.assertEqual(payment_rows(self.order),
                         (("Paid by UPI", f"{half:.2f}"), ("Paid by CASH", f"{self.total - half:.2f}")))

    def test_nothing_paid_says_nothing(self):
        self.assertEqual(payment_rows(self.order), ())

    def test_the_printed_bill_after_half_a_split_shows_the_balance(self):
        half = (self.total / 2).quantize(Decimal("0.01"))
        self.pay("upi", half)
        service = PrintingService(printer_type="network", host="192.168.1.50")
        with mock.patch("escpos.printer.Network", CapturedNetwork):
            self.assertTrue(service.print_bill(self.order))
        out = PRINTED[-1].output
        self.assertIn(b"Balance due", out)
        self.assertIn(f"{self.total - half:.2f}".encode(), out)
