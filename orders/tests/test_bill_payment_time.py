"""
The bill page lists each payment with the time it was taken.

Why: the template read p.created_at, a field Payment does not have (it is
paid_at), so every payment showed "Cash ()" with the time missing. Found by
the text floor walk (core/tests/test_text_floor.py) on 14 Nov 2026.

Run: python manage.py test orders.tests.test_bill_payment_time
"""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.tests.test_tenant_walk import Restaurant


class BillPaymentTimeTest(TestCase):

    def test_each_payment_shows_when_it_was_taken(self):
        r = Restaurant("Alpha", "paytime-alpha")
        self.client.force_login(r.owner)
        html = self.client.get(reverse("bill-view", args=[r.order.id])).content.decode()
        taken = timezone.localtime(r.payment.paid_at).strftime("%H:%M")
        self.assertIn(f'<small class="payment-time">({taken})</small>', html)
        self.assertNotIn('<small class="payment-time">()</small>', html)
