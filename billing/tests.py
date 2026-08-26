# billing/tests.py
"""
Tests for Rasova's own subscription billing app (charging tenants, not
tenant-facing). Covers:

  - SubscriptionInvoice model constraints (unique period, unique payment id,
    PROTECT on tenant delete)
  - billing/razorpay_gateway.py -- Rasova's OWN Razorpay integration, with a
    specific test that it authenticates with Rasova's own credentials and
    never a tenant's PaymentConfig (the whole reason this is a separate
    module from payments/razorpay_gateway.py)
  - billing/views.py's webhook -- signature verification, malformed/spoofed
    payloads, and idempotency under both a simple duplicate delivery and a
    genuine near-simultaneous race (the IntegrityError backstop)
  - billing/tasks.py's two Celery Beat jobs, including two N+1 regression
    tests for fixes made alongside this suite: a duplicated owner-User
    lookup in generate_monthly_invoices, and a missing select_related in
    enforce_overdue_subscriptions' warning loop
  - billing/services.py's PDF renderer (weasyprint mocked out -- this dev
    machine doesn't have the native GTK/Pango libs WeasyPrint needs, same
    pre-existing gap as the rest of the app's PDF features)
  - notifications/services/whatsapp_service.py's send_subscription_invoice
  - The superuser panel's billing actions (update_subscription, mark_paid),
    including an IDOR check on mark_paid

Run: python manage.py test billing
"""
import hashlib
import hmac
import json
import sys
import types
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

try:
    import weasyprint  # noqa: F401 -- real import, used if native libs are present
except OSError:
    # This dev machine lacks WeasyPrint's native GTK/Pango/GObject libraries
    # -- the exact same pre-existing environment gap as every other
    # WeasyPrint-based feature here (e.g. orders/views/print_views.py's bill
    # PDF), not something new. billing/services.py does `import weasyprint`
    # at module load, so without a stub even importing billing.tests would
    # fail before any test (or mock) runs. PDF tests below mock
    # billing.services.weasyprint.HTML directly and never touch real
    # rendering internals, so a bare stub is sufficient. Wherever the real
    # native libs ARE installed (e.g. a proper Linux deployment/CI), this
    # except branch never fires and the real module is used untouched.
    stub = types.ModuleType("weasyprint")
    stub.HTML = lambda *a, **k: None
    sys.modules["weasyprint"] = stub

import requests
from django.core import mail
from django.db import IntegrityError, connection
from django.db.models.deletion import ProtectedError
from django.template.loader import get_template
from django.test import TestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from tenants.models import Tenant, Outlet
from billing.models import SubscriptionInvoice
from billing.razorpay_gateway import (
    decimal_to_paise, create_subscription_payment_link, verify_webhook_signature,
)
from billing.services import render_invoice_pdf
from billing.tasks import (
    generate_monthly_invoices, enforce_overdue_subscriptions,
    OVERDUE_WARNING_DAYS, OVERDUE_SUSPEND_DAYS,
)
from notifications.services.whatsapp_service import send_subscription_invoice

WEBHOOK_SECRET = "test_rasova_webhook_secret"


def _sign(body_bytes, secret=WEBHOOK_SECRET):
    return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()


def _make_tenant(name, **kwargs):
    defaults = {"tenant_type": "cafe"}
    defaults.update(kwargs)
    return Tenant.objects.create(name=name, **defaults)


# ======================================================================
#  SubscriptionInvoice model
# ======================================================================

class SubscriptionInvoiceModelTest(TestCase):

    def setUp(self):
        self.tenant = _make_tenant("Model Test Co")

    def test_unique_period_constraint(self):
        SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
        )
        with self.assertRaises(IntegrityError):
            SubscriptionInvoice.objects.create(
                tenant=self.tenant, period_start=timezone.now().date(),
                period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
            )

    def test_razorpay_payment_id_unique(self):
        SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
            razorpay_payment_id="pay_dup",
        )
        with self.assertRaises(IntegrityError):
            SubscriptionInvoice.objects.create(
                tenant=self.tenant, period_start=timezone.now().date() + timedelta(days=30),
                period_end=timezone.now().date() + timedelta(days=60), amount=Decimal("999.00"),
                razorpay_payment_id="pay_dup",
            )

    def test_multiple_unpaid_invoices_with_null_payment_id_allowed(self):
        # null=True on a unique field -- Postgres allows many NULLs. Every
        # unpaid invoice has razorpay_payment_id=None, so this must not
        # collide as if they all shared the "same" null value.
        SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
        )
        SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date() + timedelta(days=30),
            period_end=timezone.now().date() + timedelta(days=60), amount=Decimal("999.00"),
        )
        self.assertEqual(SubscriptionInvoice.objects.count(), 2)

    def test_tenant_protected_from_delete_while_invoices_exist(self):
        SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
        )
        with self.assertRaises(ProtectedError):
            self.tenant.delete()

    def test_ordering_is_most_recent_period_first(self):
        old = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date() - timedelta(days=60),
            period_end=timezone.now().date() - timedelta(days=30), amount=Decimal("999.00"),
        )
        new = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
        )
        self.assertEqual(list(SubscriptionInvoice.objects.all()), [new, old])

    def test_str_includes_tenant_period_and_status(self):
        invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
        )
        self.assertIn(self.tenant.name, str(invoice))
        self.assertIn("pending", str(invoice))


# ======================================================================
#  billing/razorpay_gateway.py -- Rasova's OWN Razorpay integration
# ======================================================================

class RazorpayGatewayUnitTest(TestCase):

    def test_decimal_to_paise(self):
        self.assertEqual(decimal_to_paise(Decimal("999.00")), 99900)
        self.assertEqual(decimal_to_paise(Decimal("0.50")), 50)

    @override_settings(RASOVA_RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_verify_webhook_signature_valid(self):
        body = b'{"event":"payment_link.paid"}'
        self.assertTrue(verify_webhook_signature(body, _sign(body)))

    @override_settings(RASOVA_RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_verify_webhook_signature_invalid(self):
        body = b'{"event":"payment_link.paid"}'
        self.assertFalse(verify_webhook_signature(body, "wrong_signature"))

    @override_settings(RASOVA_RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET)
    def test_verify_webhook_signature_missing_header(self):
        body = b'{"event":"payment_link.paid"}'
        self.assertFalse(verify_webhook_signature(body, ""))

    @override_settings(RASOVA_RAZORPAY_WEBHOOK_SECRET="")
    def test_verify_webhook_signature_fails_closed_with_no_secret_configured(self):
        """
        If RASOVA_RAZORPAY_WEBHOOK_SECRET is ever unset in an environment,
        this must fail CLOSED (reject everything) rather than treat "" as a
        valid shared secret an attacker could trivially match.
        """
        body = b'{"event":"payment_link.paid"}'
        self.assertFalse(verify_webhook_signature(body, _sign(body, secret="")))


class CreateSubscriptionPaymentLinkTest(TestCase):

    def setUp(self):
        self.tenant = _make_tenant("Payment Link Co", subscription_fee=Decimal("999.00"))
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="plink_owner", password="x", tenant=self.tenant, outlet=self.outlet,
            role="owner", email="owner@plinkco.example",
        )
        self.invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=self.tenant.subscription_fee,
        )

    def _ok_response(self, link_id="plink_x", url="https://rzp.io/l/x"):
        resp = MagicMock(status_code=200, json=lambda: {"id": link_id, "short_url": url})
        resp.raise_for_status = lambda: None
        return resp

    @patch("billing.razorpay_gateway.requests.post")
    def test_uses_rasova_own_credentials_not_tenant_config(self, mock_post):
        """
        Security-relevant separation: this must authenticate with Rasova's
        OWN RASOVA_RAZORPAY_* settings, never a tenant's PaymentConfig keys.
        That separation is the entire reason billing/razorpay_gateway.py
        exists apart from payments/razorpay_gateway.py -- Rasova billing a
        tenant must never be able to move money through THAT tenant's own
        account.
        """
        mock_post.return_value = self._ok_response()
        with self.settings(RASOVA_RAZORPAY_KEY_ID="rasova_key", RASOVA_RAZORPAY_KEY_SECRET="rasova_secret"):
            create_subscription_payment_link(self.invoice)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["auth"], ("rasova_key", "rasova_secret"))

    @patch("billing.razorpay_gateway.requests.post")
    def test_amount_sent_in_paise_matching_invoice(self, mock_post):
        mock_post.return_value = self._ok_response()
        create_subscription_payment_link(self.invoice)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["amount"], 99900)
        self.assertEqual(kwargs["json"]["currency"], "INR")

    @patch("billing.razorpay_gateway.requests.post")
    def test_notes_identify_invoice_tenant_and_kind(self, mock_post):
        """
        The webhook trusts these notes (post signature-verification) to find
        the invoice -- kind='rasova_subscription' is what lets the webhook
        tell this apart from any other note shape sharing the account.
        """
        mock_post.return_value = self._ok_response()
        create_subscription_payment_link(self.invoice)

        notes = mock_post.call_args.kwargs["json"]["notes"]
        self.assertEqual(notes["invoice_id"], str(self.invoice.id))
        self.assertEqual(notes["tenant_id"], str(self.tenant.id))
        self.assertEqual(notes["kind"], "rasova_subscription")

    @patch("billing.razorpay_gateway.requests.post")
    def test_uses_owner_email_for_customer_contact(self, mock_post):
        mock_post.return_value = self._ok_response()
        create_subscription_payment_link(self.invoice)

        self.assertEqual(mock_post.call_args.kwargs["json"]["customer"]["email"], "owner@plinkco.example")

    @patch("billing.razorpay_gateway.requests.post")
    def test_no_owner_user_does_not_crash(self, mock_post):
        self.owner.delete()
        mock_post.return_value = self._ok_response(link_id="plink_noowner")

        link_id, link_url = create_subscription_payment_link(self.invoice)

        self.assertEqual(link_id, "plink_noowner")
        kwargs = mock_post.call_args.kwargs
        self.assertEqual(kwargs["json"]["customer"]["email"], "")
        self.assertFalse(kwargs["json"]["notify"]["email"])

    @patch("billing.razorpay_gateway.requests.post")
    @patch("accounts.models.User.objects.filter")
    def test_passing_owner_user_skips_the_extra_query(self, mock_filter, mock_post):
        """N+1-adjacent: when the caller already has the owner (as
        billing.tasks.generate_monthly_invoices now does), this must not
        look it up again."""
        mock_post.return_value = self._ok_response()
        create_subscription_payment_link(self.invoice, owner_user=self.owner)
        mock_filter.assert_not_called()

    @patch("billing.razorpay_gateway.requests.post")
    def test_api_error_propagates_for_caller_to_catch(self, mock_post):
        """generate_monthly_invoices relies on this raising so its per-tenant
        try/except can count the failure and move on to the next tenant."""
        mock_post.side_effect = requests.RequestException("Razorpay unreachable")
        with self.assertRaises(requests.RequestException):
            create_subscription_payment_link(self.invoice)


# ======================================================================
#  billing/views.py -- the webhook (security-focused)
# ======================================================================

@override_settings(RASOVA_RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET)
class BillingWebhookSecurityTest(TestCase):

    def setUp(self):
        self.tenant = _make_tenant(
            "Webhook Co", subscription_status="active", subscription_fee=Decimal("999.00"),
        )
        self.invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant,
            period_start=timezone.now().date() - timedelta(days=30),
            period_end=timezone.now().date(),
            amount=Decimal("999.00"),
            razorpay_payment_link_id="plink_abc",
        )

    def _payload(self, invoice_id="__unset__", link_id="plink_abc", payment_id="pay_1",
                 kind="rasova_subscription", event="payment_link.paid"):
        notes = {}
        if invoice_id not in ("__unset__", None):
            notes["invoice_id"] = str(invoice_id)
        if kind is not None:
            notes["kind"] = kind
        payment_entity = {"id": payment_id} if payment_id is not None else {}
        return {
            "event": event,
            "payload": {
                "payment_link": {"entity": {"id": link_id, "notes": notes}},
                "payment": {"entity": payment_entity},
            },
        }

    def _post(self, payload, signature=None):
        body = json.dumps(payload).encode()
        return self.client.post(
            reverse("billing-webhook"), data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=signature if signature is not None else _sign(body),
        )

    # ---- signature / transport ----

    def test_invalid_signature_rejected_before_any_processing(self):
        response = self._post(self._payload(invoice_id=self.invoice.id), signature="wrong")
        self.assertEqual(response.status_code, 401)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")

    def test_missing_signature_header_rejected(self):
        body = json.dumps(self._payload(invoice_id=self.invoice.id)).encode()
        response = self.client.post(reverse("billing-webhook"), data=body, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_get_request_not_allowed(self):
        response = self.client.get(reverse("billing-webhook"))
        self.assertEqual(response.status_code, 405)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            reverse("billing-webhook"), data=b"not json", content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=_sign(b"not json"),
        )
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_request_is_processed_without_login(self):
        """Razorpay's servers call this directly -- no Django session/cookie
        at all. It must work fully logged out."""
        self.client.logout()
        response = self._post(self._payload(invoice_id=self.invoice.id, payment_id="pay_anon"))
        self.assertEqual(response.status_code, 200)

    # ---- payload shape / malformed ----

    def test_unrecognized_event_is_ignored_with_200(self):
        response = self._post(self._payload(invoice_id=self.invoice.id, event="payment.failed"))
        self.assertEqual(response.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")

    def test_wrong_kind_rejected_as_malformed(self):
        """
        notes.kind must be exactly 'rasova_subscription' -- defense in depth
        against this webhook ever acting on a differently-shaped note
        payload, even one carrying a validly-signed request.
        """
        response = self._post(self._payload(invoice_id=self.invoice.id, kind="something_else"))
        self.assertEqual(response.status_code, 400)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")

    def test_missing_invoice_id_rejected(self):
        response = self._post(self._payload(invoice_id=None))
        self.assertEqual(response.status_code, 400)

    def test_non_numeric_invoice_id_rejected_as_malformed(self):
        """
        invoice_id must be validated as numeric before it reaches the ORM --
        SubscriptionInvoice.objects.get(id=invoice_id, ...) casts id via
        IntegerField.to_python, which raises an unhandled ValueError (500)
        on a non-numeric string rather than a clean 400.
        """
        response = self._post(self._payload(invoice_id="not-a-number"))
        self.assertEqual(response.status_code, 400)

    def test_missing_payment_id_rejected(self):
        response = self._post(self._payload(invoice_id=self.invoice.id, payment_id=None))
        self.assertEqual(response.status_code, 400)

    def test_invoice_id_link_id_mismatch_returns_404(self):
        """Both invoice_id AND the payment link id must match the same row
        -- guards a stale/rotated link being replayed against an invoice_id
        that's since been assigned a different link."""
        response = self._post(self._payload(invoice_id=self.invoice.id, link_id="plink_completely_different"))
        self.assertEqual(response.status_code, 404)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")

    def test_unknown_invoice_id_returns_404(self):
        response = self._post(self._payload(invoice_id=999999))
        self.assertEqual(response.status_code, 404)

    # ---- happy path ----

    def test_first_delivery_marks_invoice_paid_and_extends_tenant(self):
        response = self._post(self._payload(invoice_id=self.invoice.id, payment_id="pay_happy"))
        self.assertEqual(response.status_code, 200)

        self.invoice.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual(self.invoice.status, "paid")
        self.assertEqual(self.invoice.razorpay_payment_id, "pay_happy")
        self.assertIsNotNone(self.invoice.paid_at)
        self.assertEqual(self.tenant.subscription_end_date, self.invoice.period_end)
        self.assertEqual(self.tenant.subscription_status, "active")

    def test_paid_invoice_reactivates_suspended_tenant(self):
        self.tenant.subscription_status = "suspended"
        self.tenant.save(update_fields=["subscription_status"])

        self._post(self._payload(invoice_id=self.invoice.id, payment_id="pay_reactivate"))

        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subscription_status, "active")

    # ---- idempotency ----

    def test_duplicate_delivery_fast_path_no_double_processing(self):
        payload = self._payload(invoice_id=self.invoice.id, payment_id="pay_dupe")
        r1 = self._post(payload)
        r2 = self._post(payload)

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json().get("already_recorded"))
        self.assertEqual(SubscriptionInvoice.objects.filter(status="paid").count(), 1)

    @patch("billing.views.SubscriptionInvoice.objects.filter")
    def test_race_condition_backstop_via_integrity_error(self, mock_filter):
        """
        Simulates two near-simultaneous deliveries where the fast .exists()
        pre-check misses (both reads land before either write) -- the real
        backstop is the DB's unique constraint on razorpay_payment_id,
        caught as IntegrityError. Forced here by stubbing the pre-check to
        always report "not seen yet" while a row with that payment id
        genuinely already exists.
        """
        winner = SubscriptionInvoice.objects.create(
            tenant=self.tenant,
            period_start=timezone.now().date() - timedelta(days=60),
            period_end=timezone.now().date() - timedelta(days=30),
            amount=Decimal("999.00"), status="paid", razorpay_payment_id="pay_race",
        )
        mock_filter.return_value.exists.return_value = False  # force past the fast path

        response = self._post(self._payload(invoice_id=self.invoice.id, payment_id="pay_race"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("already_recorded"))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")  # not double-recorded
        winner.refresh_from_db()
        self.assertEqual(winner.status, "paid")


# ======================================================================
#  billing/tasks.py -- generate_monthly_invoices
# ======================================================================

class GenerateMonthlyInvoicesTaskTest(TestCase):

    def _make_active_tenant(self, name, start_days_ago=40, fee=Decimal("999.00")):
        tenant = _make_tenant(
            name, subscription_status="active", subscription_fee=fee,
            subscription_start_date=timezone.now().date() - timedelta(days=start_days_ago),
        )
        outlet = Outlet.objects.create(tenant=tenant, name="Main", phone="9999999999")
        User.objects.create_user(
            username=f"owner_{tenant.id}", password="x", tenant=tenant, outlet=outlet,
            role="owner", email=f"owner{tenant.id}@example.com",
        )
        return tenant

    def _ok_response(self, link_id="plink_task", url="https://rzp.io/l/task"):
        resp = MagicMock(status_code=200, json=lambda: {"id": link_id, "short_url": url})
        resp.raise_for_status = lambda: None
        return resp

    @patch("billing.services.render_invoice_pdf", return_value=b"%PDF-fake%")
    @patch("billing.razorpay_gateway.requests.post")
    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_creates_invoice_for_due_active_tenant(self, mock_twilio, mock_meta, mock_post, mock_pdf):
        tenant = self._make_active_tenant("Due Co")
        mock_post.return_value = self._ok_response()

        result = generate_monthly_invoices()

        self.assertEqual(result["created"], 1)
        invoice = SubscriptionInvoice.objects.get(tenant=tenant)
        self.assertEqual(invoice.razorpay_payment_link_id, "plink_task")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(tenant.name, mail.outbox[0].subject)
        self.assertEqual(len(mail.outbox[0].attachments), 1)

    @patch("billing.services.render_invoice_pdf", return_value=b"%PDF-fake%")
    @patch("billing.razorpay_gateway.requests.post")
    def test_tenant_with_future_start_date_is_skipped(self, mock_post, mock_pdf):
        self._make_active_tenant("Future Co", start_days_ago=-10)  # starts 10 days from now
        mock_post.return_value = self._ok_response()

        result = generate_monthly_invoices()

        self.assertEqual(result["created"], 0)
        self.assertEqual(SubscriptionInvoice.objects.count(), 0)

    def test_trial_and_suspended_tenants_are_skipped(self):
        _make_tenant("Trial Co", subscription_status="trial")
        _make_tenant("Suspended Co", subscription_status="suspended")

        result = generate_monthly_invoices()

        self.assertEqual(result["created"], 0)
        self.assertEqual(SubscriptionInvoice.objects.count(), 0)

    @patch("billing.services.render_invoice_pdf", return_value=b"%PDF-fake%")
    @patch("billing.razorpay_gateway.requests.post")
    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_already_billed_period_is_not_regenerated(self, mock_twilio, mock_meta, mock_post, mock_pdf):
        # start_days_ago=10 -> first invoice's period_end lands ~20 days in
        # the future, so a second run has nothing newly due yet.
        self._make_active_tenant("Once Co", start_days_ago=10)
        mock_post.return_value = self._ok_response()

        generate_monthly_invoices()
        result2 = generate_monthly_invoices()

        self.assertEqual(result2["created"], 0)
        self.assertEqual(SubscriptionInvoice.objects.count(), 1)

    @patch("billing.services.render_invoice_pdf", return_value=b"%PDF-fake%")
    @patch("billing.razorpay_gateway.requests.post")
    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_one_tenant_razorpay_failure_does_not_block_the_batch(self, mock_twilio, mock_meta, mock_post, mock_pdf):
        broken = self._make_active_tenant("Broken Co")
        healthy = self._make_active_tenant("Healthy Co")

        def side_effect(url, **kwargs):
            if kwargs["json"]["notes"]["tenant_id"] == str(broken.id):
                raise requests.RequestException("Razorpay API down")
            return self._ok_response()

        mock_post.side_effect = side_effect

        result = generate_monthly_invoices()

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertFalse(SubscriptionInvoice.objects.filter(tenant=broken).exists())
        self.assertTrue(SubscriptionInvoice.objects.filter(tenant=healthy).exists())

    @patch("billing.razorpay_gateway.requests.post")
    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_pdf_import_failure_does_not_crash_the_whole_batch(self, mock_twilio, mock_meta, mock_post):
        """
        Regression test mirroring the real bug fixed in
        inventory.views.mark_po_ordered: render_invoice_pdf's import used to
        happen once, before this function's tenant loop even started.
        billing.services imports weasyprint at module load -- on a server
        missing its native GTK/Pango libs, that import failing used to crash
        the entire monthly billing run before a single tenant was processed,
        not just the one PDF that couldn't render. The import now happens
        per-tenant, inside the same try/except that already isolates one
        tenant's failure (see the Razorpay-failure test above) from the rest
        of the batch.
        """
        self._make_active_tenant("Due Co")
        mock_post.return_value = self._ok_response()

        with patch.dict(sys.modules, {"billing.services": None}):
            result = generate_monthly_invoices()  # must not raise

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["failed"], 1)

    @patch("billing.services.render_invoice_pdf", return_value=b"%PDF-fake%")
    @patch("billing.razorpay_gateway.requests.post")
    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_owner_lookup_not_duplicated_per_tenant(self, mock_twilio, mock_meta, mock_post, mock_pdf):
        """
        N+1 regression: create_subscription_payment_link used to look up
        the tenant's owner User itself, and the task looked it up AGAIN
        right after for the email step -- two queries per tenant for the
        same row. The task now fetches it once and passes it through.
        """
        self._make_active_tenant("Query Co 1")
        self._make_active_tenant("Query Co 2")
        mock_post.return_value = self._ok_response()

        with CaptureQueriesContext(connection) as ctx:
            generate_monthly_invoices()

        owner_queries = [
            q for q in ctx.captured_queries
            if "accounts_user" in q["sql"].lower() and "role" in q["sql"].lower()
        ]
        self.assertEqual(len(owner_queries), 2)  # exactly one per tenant, not two


# ======================================================================
#  billing/tasks.py -- enforce_overdue_subscriptions
# ======================================================================

class EnforceOverdueSubscriptionsTaskTest(TestCase):

    def setUp(self):
        self.tenant = _make_tenant("Overdue Co", subscription_status="active", subscription_fee=Decimal("999.00"))
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", phone="9999999999")

    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_pending_invoice_past_period_end_becomes_overdue(self, mock_twilio, mock_meta):
        invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant,
            period_start=timezone.now().date() - timedelta(days=35),
            period_end=timezone.now().date() - timedelta(days=1),
            amount=Decimal("999.00"), status="pending",
        )
        enforce_overdue_subscriptions()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "overdue")

    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_pending_invoice_not_yet_past_period_end_is_untouched(self, mock_twilio, mock_meta):
        invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant,
            period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30),
            amount=Decimal("999.00"), status="pending",
        )
        enforce_overdue_subscriptions()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "pending")

    @patch("notifications.services.whatsapp_service._send_meta", return_value=True)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_warning_sent_once_past_warning_cutoff(self, mock_twilio, mock_meta):
        invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant,
            period_start=timezone.now().date() - timedelta(days=40),
            period_end=timezone.now().date() - timedelta(days=OVERDUE_WARNING_DAYS + 1),
            amount=Decimal("999.00"), status="overdue",
        )
        result = enforce_overdue_subscriptions()
        invoice.refresh_from_db()
        self.assertEqual(result["warned"], 1)
        self.assertIsNotNone(invoice.overdue_warning_sent_at)
        self.assertEqual(mock_meta.call_count, 1)

    @patch("notifications.services.whatsapp_service._send_meta", return_value=True)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_warning_never_sent_twice(self, mock_twilio, mock_meta):
        SubscriptionInvoice.objects.create(
            tenant=self.tenant,
            period_start=timezone.now().date() - timedelta(days=40),
            period_end=timezone.now().date() - timedelta(days=OVERDUE_WARNING_DAYS + 1),
            amount=Decimal("999.00"), status="overdue",
        )
        enforce_overdue_subscriptions()
        result2 = enforce_overdue_subscriptions()

        self.assertEqual(result2["warned"], 0)
        self.assertEqual(mock_meta.call_count, 1)

    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_tenant_auto_suspended_past_suspend_cutoff(self, mock_twilio, mock_meta):
        SubscriptionInvoice.objects.create(
            tenant=self.tenant,
            period_start=timezone.now().date() - timedelta(days=60),
            period_end=timezone.now().date() - timedelta(days=OVERDUE_SUSPEND_DAYS + 1),
            amount=Decimal("999.00"), status="overdue",
        )
        result = enforce_overdue_subscriptions()
        self.tenant.refresh_from_db()
        self.assertEqual(result["suspended"], 1)
        self.assertEqual(self.tenant.subscription_status, "suspended")

    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_not_yet_past_suspend_cutoff_stays_active(self, mock_twilio, mock_meta):
        SubscriptionInvoice.objects.create(
            tenant=self.tenant,
            period_start=timezone.now().date() - timedelta(days=20),
            period_end=timezone.now().date() - timedelta(days=OVERDUE_WARNING_DAYS + 1),
            amount=Decimal("999.00"), status="overdue",
        )
        enforce_overdue_subscriptions()
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subscription_status, "active")

    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_paid_and_waived_invoices_are_never_touched(self, mock_twilio, mock_meta):
        paid = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date() - timedelta(days=60),
            period_end=timezone.now().date() - timedelta(days=30), amount=Decimal("999.00"),
            status="paid",
        )
        waived = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date() - timedelta(days=30),
            period_end=timezone.now().date() - timedelta(days=1), amount=Decimal("999.00"),
            status="waived",
        )
        enforce_overdue_subscriptions()
        paid.refresh_from_db()
        waived.refresh_from_db()
        self.assertEqual(paid.status, "paid")
        self.assertEqual(waived.status, "waived")

    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    def test_warning_loop_does_not_requery_tenant_per_invoice(self, mock_twilio, mock_meta):
        """
        N+1 regression: send_subscription_invoice() and its message builder
        both read invoice.tenant. Without select_related("tenant") on the
        warning queryset, each of these 5 invoices (all the same tenant)
        would trigger its own separate SELECT against tenants_tenant.
        """
        for i in range(5):
            SubscriptionInvoice.objects.create(
                tenant=self.tenant,
                period_start=timezone.now().date() - timedelta(days=40 + i),
                period_end=timezone.now().date() - timedelta(days=OVERDUE_WARNING_DAYS + 1 + i),
                amount=Decimal("999.00"), status="overdue",
            )

        with CaptureQueriesContext(connection) as ctx:
            enforce_overdue_subscriptions()

        tenant_queries = [q for q in ctx.captured_queries if "tenants_tenant" in q["sql"].lower()]
        # 1 query for the select_related JOIN (any invoice count) + at most
        # 1 for the separate suspend-eligibility Tenant filter. A regression
        # back to per-invoice `invoice.tenant` access would scale this with
        # invoice count instead (5+ here).
        self.assertLessEqual(len(tenant_queries), 2)


# ======================================================================
#  billing/services.py -- PDF generation (weasyprint mocked -- this dev
#  machine lacks the native GTK/Pango libs, a pre-existing environment gap
#  shared with orders/views/print_views.py's bill PDF, not new)
# ======================================================================

class RenderInvoicePdfServiceTest(TestCase):

    def setUp(self):
        self.tenant = _make_tenant("PDF Co")
        self.invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
        )

    @patch("billing.services.weasyprint.HTML")
    def test_calls_weasyprint_with_presentational_hints_disabled(self, mock_html_cls):
        """
        presentational_hints=False is the actual fix for the WeasyPrint
        CSS-injection CVE (GHSA-jhhc-3hcp-qhm5) -- this invoice renders a
        tenant-supplied name, the same class of guest-influenced content the
        CVE affects. Must never regress to the (vulnerable) default of True.
        """
        mock_html_cls.return_value.write_pdf.return_value = b"%PDF-fake%"

        result = render_invoice_pdf(self.invoice, payment_link_url="https://rzp.io/l/x")

        self.assertEqual(result, b"%PDF-fake%")
        _, kwargs = mock_html_cls.return_value.write_pdf.call_args
        self.assertIs(kwargs["presentational_hints"], False)

    @patch("billing.services.weasyprint.HTML")
    def test_template_renders_tenant_name_and_amount(self, mock_html_cls):
        mock_html_cls.return_value.write_pdf.return_value = b"%PDF-fake%"

        render_invoice_pdf(self.invoice)

        rendered_html = mock_html_cls.call_args.kwargs["string"]
        self.assertIn(self.tenant.name, rendered_html)
        self.assertIn("999.00", rendered_html)

    def test_invoice_template_loads_without_syntax_error(self):
        get_template("billing/invoice.html")  # raises TemplateSyntaxError if malformed


# ======================================================================
#  notifications/services/whatsapp_service.py -- send_subscription_invoice
# ======================================================================

class SendSubscriptionInvoiceWhatsAppTest(TestCase):

    def setUp(self):
        self.tenant = _make_tenant("WA Co")
        self.invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date(),
            period_end=timezone.now().date() + timedelta(days=30), amount=Decimal("999.00"),
        )

    def test_no_outlet_returns_false(self):
        self.assertFalse(send_subscription_invoice(self.invoice))

    def test_outlet_without_phone_returns_false(self):
        Outlet.objects.create(tenant=self.tenant, name="Main", phone="")
        self.assertFalse(send_subscription_invoice(self.invoice))

    @patch("notifications.services.whatsapp_service._send_meta", return_value=True)
    def test_sends_via_meta_when_available(self, mock_meta):
        Outlet.objects.create(tenant=self.tenant, name="Main", phone="9876543210")
        result = send_subscription_invoice(self.invoice, payment_link_url="https://rzp.io/l/x")

        self.assertTrue(result)
        mock_meta.assert_called_once()
        phone_arg, message_arg = mock_meta.call_args[0]
        self.assertEqual(phone_arg, "+919876543210")
        self.assertIn(self.tenant.name, message_arg)
        self.assertIn("https://rzp.io/l/x", message_arg)

    @patch("notifications.services.whatsapp_service._send_twilio", return_value=True)
    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    def test_falls_back_to_twilio_when_meta_fails(self, mock_meta, mock_twilio):
        Outlet.objects.create(tenant=self.tenant, name="Main", phone="9876543210")
        result = send_subscription_invoice(self.invoice)

        self.assertTrue(result)
        mock_twilio.assert_called_once()

    @patch("notifications.services.whatsapp_service._send_twilio", return_value=False)
    @patch("notifications.services.whatsapp_service._send_meta", return_value=False)
    def test_returns_false_when_both_backends_fail(self, mock_meta, mock_twilio):
        Outlet.objects.create(tenant=self.tenant, name="Main", phone="9876543210")
        self.assertFalse(send_subscription_invoice(self.invoice))

    def test_survives_network_failure_without_raising(self):
        """
        Realistic failure: Meta's API is unreachable. _send_meta's own
        try/except is what catches it -- send_subscription_invoice (like
        send_bill_receipt) has no outer try/except of its own, so the
        "never raises" contract depends entirely on that inner catch.
        """
        Outlet.objects.create(tenant=self.tenant, name="Main", phone="9876543210")
        with self.settings(META_WHATSAPP_TOKEN="tok", META_WHATSAPP_PHONE_ID="123"):
            with patch(
                "notifications.services.whatsapp_service.urllib.request.urlopen",
                side_effect=OSError("network down"),
            ):
                result = send_subscription_invoice(self.invoice)
        self.assertFalse(result)


# ======================================================================
#  Superuser panel -- billing actions (accounts/views/superuser_views.py)
# ======================================================================

class SuperuserBillingActionsTest(TestCase):

    def setUp(self):
        self.tenant = _make_tenant("SU Billing Co", subscription_status="trial", subscription_fee=Decimal("0.00"))
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.superuser = User.objects.create_user(
            username="su_billing", password="pw", is_superuser=True, is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="su_billing_owner", password="pw", tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.invoice = SubscriptionInvoice.objects.create(
            tenant=self.tenant, period_start=timezone.now().date() - timedelta(days=30),
            period_end=timezone.now().date(), amount=Decimal("999.00"),
        )

    def test_non_superuser_cannot_view_tenant_config(self):
        self.client.login(username="su_billing_owner", password="pw")
        response = self.client.get(reverse("superuser_tenant", args=[self.tenant.id]))
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_update_subscription_fee_and_status(self):
        self.client.login(username="su_billing", password="pw")
        response = self.client.post(
            reverse("superuser_tenant", args=[self.tenant.id]),
            {"action": "update_subscription", "subscription_fee": "2499.00", "subscription_status": "active"},
        )
        self.assertEqual(response.status_code, 302)
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subscription_fee, Decimal("2499.00"))
        self.assertEqual(self.tenant.subscription_status, "active")

    def test_invalid_subscription_status_is_ignored(self):
        self.client.login(username="su_billing", password="pw")
        self.client.post(
            reverse("superuser_tenant", args=[self.tenant.id]),
            {"action": "update_subscription", "subscription_fee": "999.00", "subscription_status": "not_a_real_status"},
        )
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.subscription_status, "trial")  # unchanged

    def test_superuser_can_mark_invoice_paid_manually(self):
        self.client.login(username="su_billing", password="pw")
        response = self.client.post(
            reverse("superuser_tenant", args=[self.tenant.id]),
            {"action": "mark_paid", "invoice_id": self.invoice.id},
        )
        self.assertEqual(response.status_code, 302)
        self.invoice.refresh_from_db()
        self.tenant.refresh_from_db()
        self.assertEqual(self.invoice.status, "paid")
        self.assertEqual(self.tenant.subscription_end_date, self.invoice.period_end)
        self.assertEqual(self.tenant.subscription_status, "active")

    def test_cannot_mark_paid_an_invoice_belonging_to_a_different_tenant(self):
        """
        IDOR check: mark_paid is scoped to the tenant in the URL. Passing
        another tenant's invoice_id must 404, not mark that tenant's
        invoice paid.
        """
        other_tenant = _make_tenant("Other SU Co")
        other_invoice = SubscriptionInvoice.objects.create(
            tenant=other_tenant, period_start=timezone.now().date() - timedelta(days=30),
            period_end=timezone.now().date(), amount=Decimal("999.00"),
        )
        self.client.login(username="su_billing", password="pw")
        response = self.client.post(
            reverse("superuser_tenant", args=[self.tenant.id]),   # URL says self.tenant
            {"action": "mark_paid", "invoice_id": other_invoice.id},  # invoice belongs elsewhere
        )
        self.assertEqual(response.status_code, 404)
        other_invoice.refresh_from_db()
        self.assertEqual(other_invoice.status, "pending")

    def test_non_superuser_cannot_mark_invoice_paid(self):
        self.client.login(username="su_billing_owner", password="pw")
        response = self.client.post(
            reverse("superuser_tenant", args=[self.tenant.id]),
            {"action": "mark_paid", "invoice_id": self.invoice.id},
        )
        self.assertEqual(response.status_code, 403)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, "pending")

    def test_invoices_listed_scoped_to_tenant_only(self):
        other_tenant = _make_tenant("Listing Other Co")
        SubscriptionInvoice.objects.create(
            tenant=other_tenant, period_start=timezone.now().date() - timedelta(days=30),
            period_end=timezone.now().date(), amount=Decimal("999.00"),
        )
        self.client.login(username="su_billing", password="pw")
        response = self.client.get(reverse("superuser_tenant", args=[self.tenant.id]))
        self.assertEqual(list(response.context["invoices"]), [self.invoice])
