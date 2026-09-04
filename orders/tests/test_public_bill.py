"""
Tests for the public WhatsApp bill link and its feature gating.

Run: python manage.py test orders.tests.test_public_bill
"""
import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from orders.models import Payment
from orders.tests.test_billing_modes import CounterBillingBase
from orders.views.public_views import make_public_bill_token
from tenants.models import TenantFeatureOverride


class PublicBillViewTest(CounterBillingBase):

    def test_valid_token_renders_bill_no_login_required(self):
        order = self._make_order()
        token = make_public_bill_token(order.id)
        self.client.logout()  # public link must work logged-out

        response = self.client.get(reverse("public-bill", args=[token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.tenant.name)
        self.assertContains(response, "Idli")

    def test_tampered_token_returns_expired_page(self):
        order = self._make_order()
        token = make_public_bill_token(order.id)
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        self.client.logout()

        response = self.client.get(reverse("public-bill", args=[tampered]))

        self.assertEqual(response.status_code, 400)

    def test_expired_token_returns_410(self):
        order = self._make_order()
        token = make_public_bill_token(order.id)
        self.client.logout()

        with patch("orders.views.public_views.PUBLIC_BILL_MAX_AGE", -1):
            response = self.client.get(reverse("public-bill", args=[token]))

        self.assertEqual(response.status_code, 410)

    def test_remaining_balance_shown_for_unpaid_order(self):
        order = self._make_order()
        token = make_public_bill_token(order.id)
        self.client.logout()

        response = self.client.get(reverse("public-bill", args=[token]))

        self.assertEqual(response.context["remaining"], order.grand_total)

    def test_paid_in_full_after_payment_recorded(self):
        order = self._make_order()
        Payment.objects.create(order=order, method="cash", amount=order.grand_total)
        token = make_public_bill_token(order.id)
        self.client.logout()

        response = self.client.get(reverse("public-bill", args=[token]))

        self.assertEqual(response.context["remaining"], 0)
        self.assertContains(response, "Paid in full")


class WhatsAppDispatchGatingTest(CounterBillingBase):
    """
    whatsapp_receipts is a custom-only feature (core/features.py) — off for
    every tenant until a TenantFeatureOverride turns it on. pay_order's
    post-payment WhatsApp dispatch must respect that gate.
    """

    def _pay_in_full(self, order):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("pay-order", args=[order.id]),
                data=json.dumps({"method": "cash", "amount": str(order.grand_total)}),
                content_type="application/json",
            )

    @patch("notifications.tasks.send_whatsapp_receipt_task.apply_async")
    def test_whatsapp_not_dispatched_when_feature_off(self, mock_apply_async):
        order = self._make_order()
        order.customer_phone = "9876543210"
        order.save(update_fields=["customer_phone"])

        response = self._pay_in_full(order)

        self.assertEqual(response.status_code, 200)
        mock_apply_async.assert_not_called()

    @patch("notifications.tasks.send_whatsapp_receipt_task.apply_async")
    def test_whatsapp_dispatched_when_feature_enabled(self, mock_apply_async):
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="whatsapp_receipts", enabled=True
        )
        order = self._make_order()
        order.customer_phone = "9876543210"
        order.save(update_fields=["customer_phone"])

        response = self._pay_in_full(order)

        self.assertEqual(response.status_code, 200)
        mock_apply_async.assert_called_once()
        # dispatch() (core/celery_utils.py) calls apply_async(args=(...), kwargs={...},
        # headers={...}) rather than .delay(...) directly, so the order id/bill url
        # land in the args= kwarg, not positional call args.
        called_order_id, called_bill_url = mock_apply_async.call_args.kwargs["args"]
        self.assertEqual(called_order_id, order.id)
        self.assertIn("/bill/public/", called_bill_url)
