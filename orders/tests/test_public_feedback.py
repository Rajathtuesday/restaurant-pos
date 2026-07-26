"""
Tests for the public guest-feedback flow (star rating + comment) reached from
the "Rate Your Experience" link on the public bill.

guest_feedback is a custom-only feature (core/features.py) -- off for every
tenant by default. Before this feature, GuestFeedback (crm/feedback_models.py)
had no view or URL anywhere that could create one.

Run: python manage.py test orders.tests.test_public_feedback
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from crm.feedback_models import GuestFeedback
from orders.models import Payment
from orders.tests.test_billing_modes import CounterBillingBase
from orders.views.public_views import make_public_bill_token
from tenants.models import TenantFeatureOverride


class GuestFeedbackFeatureGateTest(CounterBillingBase):

    def _feedback_url(self, order):
        token = make_public_bill_token(order.id)
        return reverse("public-feedback", args=[token])

    def test_not_available_when_feature_off(self):
        order = self._make_order()
        self.client.logout()

        response = self.client.get(self._feedback_url(order))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "isn't available")
        self.assertFalse(GuestFeedback.objects.filter(order=order).exists())

    def test_link_hidden_on_bill_when_feature_off(self):
        order = self._make_order()
        Payment.objects.create(order=order, method="cash", amount=order.grand_total)
        token = make_public_bill_token(order.id)
        self.client.logout()

        response = self.client.get(reverse("public-bill", args=[token]))

        self.assertNotContains(response, "Rate Your Experience")

    def test_link_shown_on_paid_bill_when_feature_on(self):
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="guest_feedback", enabled=True
        )
        order = self._make_order()
        Payment.objects.create(order=order, method="cash", amount=order.grand_total)
        token = make_public_bill_token(order.id)
        self.client.logout()

        response = self.client.get(reverse("public-bill", args=[token]))

        self.assertContains(response, "Rate Your Experience")

    def test_link_hidden_when_bill_not_fully_paid(self):
        """Asking for a rating before the bill is settled makes no sense --
        the feedback link only appears once remaining <= 0."""
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="guest_feedback", enabled=True
        )
        order = self._make_order()  # unpaid
        token = make_public_bill_token(order.id)
        self.client.logout()

        response = self.client.get(reverse("public-bill", args=[token]))

        self.assertNotContains(response, "Rate Your Experience")


class GuestFeedbackSubmissionTest(CounterBillingBase):

    def setUp(self):
        super().setUp()
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="guest_feedback", enabled=True
        )

    def _feedback_url(self, order):
        token = make_public_bill_token(order.id)
        return reverse("public-feedback", args=[token])

    def test_valid_rating_creates_feedback_row(self):
        order = self._make_order()
        self.client.logout()

        response = self.client.post(self._feedback_url(order), {
            "rating": "4", "guest_name": "Asha", "comment": "Great service",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thanks for letting us know")
        fb = GuestFeedback.objects.get(order=order)
        self.assertEqual(fb.rating, 4)
        self.assertEqual(fb.guest_name, "Asha")
        self.assertEqual(fb.comment, "Great service")
        self.assertEqual(fb.tenant_id, self.tenant.id)
        self.assertEqual(fb.outlet_id, self.outlet.id)

    def test_missing_rating_rejected_no_row_created(self):
        order = self._make_order()
        self.client.logout()

        response = self.client.post(self._feedback_url(order), {"comment": "no rating given"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please pick a rating")
        self.assertFalse(GuestFeedback.objects.filter(order=order).exists())

    def test_out_of_range_rating_rejected(self):
        order = self._make_order()
        self.client.logout()

        response = self.client.post(self._feedback_url(order), {"rating": "9"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please pick a rating")
        self.assertFalse(GuestFeedback.objects.filter(order=order).exists())

    def test_second_submission_for_same_order_blocked(self):
        order = self._make_order()
        GuestFeedback.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            guest_name="First", rating=5,
        )
        self.client.logout()

        response = self.client.post(self._feedback_url(order), {"rating": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already rated")
        # The second (differing) rating must NOT have overwritten or duplicated.
        self.assertEqual(GuestFeedback.objects.filter(order=order).count(), 1)
        self.assertEqual(GuestFeedback.objects.get(order=order).rating, 5)

    def test_get_shows_already_submitted_state(self):
        order = self._make_order()
        GuestFeedback.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order, rating=3,
        )
        self.client.logout()

        response = self.client.get(self._feedback_url(order))

        self.assertContains(response, "already rated")

    def test_expired_token_rejected(self):
        order = self._make_order()
        token = make_public_bill_token(order.id)
        self.client.logout()

        with patch("orders.views.public_views.PUBLIC_BILL_MAX_AGE", -1):
            response = self.client.post(reverse("public-feedback", args=[token]), {"rating": "5"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(GuestFeedback.objects.filter(order=order).exists())
