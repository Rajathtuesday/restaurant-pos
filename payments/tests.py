# payments/tests.py
"""
Tests for the payments app (Phase 6 of the orders app split, the final
phase): RazorpayQRCode (UPI QR gateway) and Refund.

Combines, verbatim except for import-path/mock-path fixes:
  - orders/tests/test_razorpay.py (5 of 6 classes moved -- ProcessPaymentReferenceTest
    tests orders.services.payment_service.process_payment generically and
    moved into orders/tests/test_billing_modes.py instead, not here)
  - orders/tests/test_refunds.py (moved wholesale)
  - orders/tests/test_critical.py::RefundDefaultStatusTest (moved)
  - orders/tests/test_security_fixes.py::RefundCrossTenantIDORTest (moved)

Plus one new test closing a gap found during the move: orders' refund_payment
view (the cashier-facing "request a refund" entry point, a reverse
dependency -- orders calling into payments) had zero test coverage anywhere.

Run: python manage.py test payments
"""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from orders.models import Order, OrderEvent, Payment, Table
from orders.services.payment_service import process_payment
from orders.tests.test_billing_modes import CounterBillingBase
from payments.models import RazorpayQRCode, Refund
from payments.razorpay_gateway import (
    decimal_to_paise, paise_to_decimal, verify_webhook_signature, create_qr_payment,
)
from payments.refund_service import process_refund, approve_refund, reject_refund
from setup.models import PaymentConfig
from tenants.models import Tenant, Outlet, TenantFeatureOverride

WEBHOOK_SECRET = "test_webhook_secret"


def _sign(body_str, secret=WEBHOOK_SECRET):
    return hmac.new(secret.encode(), body_str.encode(), hashlib.sha256).hexdigest()


# ======================================================================
#  Moved from orders/tests/test_razorpay.py
# ======================================================================

class RazorpayGatewayUnitTest(TestCase):

    def test_decimal_to_paise(self):
        self.assertEqual(decimal_to_paise(Decimal("152.00")), 15200)
        self.assertEqual(decimal_to_paise(Decimal("0.50")), 50)

    def test_paise_to_decimal(self):
        self.assertEqual(paise_to_decimal(15200), Decimal("152"))

    def test_round_trip(self):
        amount = Decimal("499.50")
        self.assertEqual(paise_to_decimal(decimal_to_paise(amount)), amount)

    def test_verify_webhook_signature_valid(self):
        body = b'{"event":"payment.captured"}'
        sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(body, sig, "secret"))

    def test_verify_webhook_signature_invalid(self):
        body = b'{"event":"payment.captured"}'
        self.assertFalse(verify_webhook_signature(body, "wrong_sig", "secret"))

    def test_verify_webhook_signature_missing_secret(self):
        body = b'{"event":"payment.captured"}'
        sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertFalse(verify_webhook_signature(body, sig, ""))


class CreateQRPaymentServiceTest(CounterBillingBase):

    def setUp(self):
        super().setUp()
        self.config = PaymentConfig.objects.get(outlet=self.outlet)
        self.config.razorpay_enabled = True
        self.config.razorpay_key_id = "rzp_test_key"
        self.config.razorpay_key_secret = "rzp_test_secret"
        self.config.razorpay_webhook_secret = WEBHOOK_SECRET
        self.config.save()

    @patch("payments.razorpay_gateway.requests.post")
    def test_create_qr_payment_creates_tracking_row(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "qr_test123", "image_url": "https://rzp.io/qr_test123.png"},
        )
        mock_post.return_value.raise_for_status = lambda: None

        order = self._make_order()
        qr = create_qr_payment(order, self.config)

        self.assertEqual(qr.qr_code_id, "qr_test123")
        self.assertEqual(qr.quoted_amount, order.grand_total)
        self.assertEqual(qr.status, "active")
        self.assertEqual(RazorpayQRCode.objects.count(), 1)

    @patch("payments.razorpay_gateway.requests.post")
    def test_create_qr_uses_basic_auth_with_outlet_keys(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "qr_x", "image_url": "https://rzp.io/qr_x.png"},
        )
        mock_post.return_value.raise_for_status = lambda: None

        order = self._make_order()
        create_qr_payment(order, self.config)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["auth"], ("rzp_test_key", "rzp_test_secret"))
        self.assertEqual(kwargs["json"]["notes"]["order_id"], str(order.id))
        self.assertEqual(kwargs["json"]["notes"]["tenant_id"], str(order.tenant_id))
        self.assertEqual(kwargs["json"]["notes"]["outlet_id"], str(order.outlet_id))


class CreateRazorpayQRViewTest(CounterBillingBase):

    def setUp(self):
        super().setUp()
        self.config = PaymentConfig.objects.get(outlet=self.outlet)
        self.config.razorpay_enabled = True
        self.config.razorpay_key_id = "rzp_test_key"
        self.config.razorpay_key_secret = "rzp_test_secret"
        self.config.save()

    def test_blocked_when_feature_not_enabled(self):
        order = self._make_order()
        response = self.client.post(reverse("razorpay-create-qr", args=[order.id]))
        self.assertEqual(response.status_code, 403)

    def test_blocked_when_razorpay_not_enabled_on_outlet(self):
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="razorpay_gateway", enabled=True)
        self.config.razorpay_enabled = False
        self.config.save()
        order = self._make_order()

        response = self.client.post(reverse("razorpay-create-qr", args=[order.id]))

        self.assertEqual(response.status_code, 400)

    @patch("payments.razorpay_views.create_qr_payment")
    def test_returns_qr_details_when_enabled(self, mock_create):
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="razorpay_gateway", enabled=True)
        order = self._make_order()
        mock_create.return_value = RazorpayQRCode.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            qr_code_id="qr_abc", image_url="https://rzp.io/qr_abc.png",
            quoted_amount=order.grand_total, expires_at=timezone.now(),
        )

        response = self.client.post(reverse("razorpay-create-qr", args=[order.id]))

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["qr_code_id"], "qr_abc")


class RazorpayWebhookTest(CounterBillingBase):

    def setUp(self):
        super().setUp()
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="razorpay_gateway", enabled=True)
        self.config = PaymentConfig.objects.get(outlet=self.outlet)
        self.config.razorpay_enabled = True
        self.config.razorpay_key_id = "rzp_test_key"
        self.config.razorpay_key_secret = "rzp_test_secret"
        self.config.razorpay_webhook_secret = WEBHOOK_SECRET
        self.config.save()
        self.client.logout()  # webhook is unauthenticated

    def _webhook_url(self):
        return reverse("razorpay-webhook") + f"?tenant_id={self.tenant.id}&outlet_id={self.outlet.id}"

    def _credited_payload(self, order, payment_id="pay_xyz", amount_paise=None, outlet_id=None, tenant_id=None):
        if amount_paise is None:
            amount_paise = decimal_to_paise(order.grand_total)
        return {
            "event": "qr_code.credited",
            "payload": {
                "qr_code": {"entity": {
                    "id": "qr_xyz",
                    "notes": {
                        "order_id": str(order.id),
                        "tenant_id": str(tenant_id or self.tenant.id),
                        "outlet_id": str(outlet_id or self.outlet.id),
                    },
                }},
                "payment": {"entity": {"id": payment_id, "amount": amount_paise}},
            },
        }

    def _post_webhook(self, payload):
        body = json.dumps(payload)
        return self.client.post(
            self._webhook_url(), data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=_sign(body),
        )

    def test_missing_outlet_id_returns_400(self):
        response = self.client.post(reverse("razorpay-webhook"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_signature_returns_401(self):
        order = self._make_order()
        body = json.dumps(self._credited_payload(order))
        response = self.client.post(
            self._webhook_url(), data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE="wrong",
        )
        self.assertEqual(response.status_code, 401)

    def test_feature_disabled_returns_403(self):
        TenantFeatureOverride.objects.filter(tenant=self.tenant, feature="razorpay_gateway").update(enabled=False)
        order = self._make_order()
        response = self._post_webhook(self._credited_payload(order))
        self.assertEqual(response.status_code, 403)

    def test_credited_event_records_payment_and_closes_order(self):
        order = self._make_order()
        response = self._post_webhook(self._credited_payload(order, payment_id="pay_full"))

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "closed")
        payment = Payment.objects.get(reference="pay_full")
        self.assertEqual(payment.method, "upi")
        self.assertEqual(payment.amount, order.grand_total)

    def test_credited_event_moves_table_to_free(self):
        """
        Mirrors pay_order's behaviour (payment_views.py) for the cashier-driven
        flow — a customer paying via Razorpay QR with no cashier involved must
        still trigger the same table lifecycle transition.
        """
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1", state="billing")
        order = self._make_order()
        order.table = table
        order.save(update_fields=["table"])

        self._post_webhook(self._credited_payload(order, payment_id="pay_table_test"))

        table.refresh_from_db()
        self.assertEqual(table.state, "free")

    def test_qr_marked_paid_after_credit(self):
        order = self._make_order()
        qr = RazorpayQRCode.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            qr_code_id="qr_xyz", image_url="https://rzp.io/x.png",
            quoted_amount=order.grand_total, status="active", expires_at=timezone.now(),
        )
        self._post_webhook(self._credited_payload(order, payment_id="pay_q1"))
        qr.refresh_from_db()
        self.assertEqual(qr.status, "paid")

    def test_idempotent_replay_does_not_double_record(self):
        order = self._make_order()
        payload = self._credited_payload(order, payment_id="pay_dupe")

        r1 = self._post_webhook(payload)
        r2 = self._post_webhook(payload)

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(Payment.objects.filter(reference="pay_dupe").count(), 1)

    def test_outlet_mismatch_rejected(self):
        order = self._make_order()
        payload = self._credited_payload(order, outlet_id=99999)
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 400)

    def test_tenant_mismatch_rejected(self):
        """
        Same outlet_id, but notes claim a different tenant_id — must be
        rejected even though outlet_id alone would otherwise resolve fine.
        """
        order = self._make_order()
        payload = self._credited_payload(order, tenant_id=99999)
        response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 400)

    def test_webhook_url_requires_tenant_id(self):
        order = self._make_order()
        url = reverse("razorpay-webhook") + f"?outlet_id={self.outlet.id}"  # no tenant_id
        body = json.dumps(self._credited_payload(order))
        response = self.client.post(
            url, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=_sign(body),
        )
        self.assertEqual(response.status_code, 400)

    def test_qr_code_closed_marks_expired(self):
        order = self._make_order()
        qr = RazorpayQRCode.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            qr_code_id="qr_closeme", image_url="https://rzp.io/x.png",
            quoted_amount=order.grand_total, status="active", expires_at=timezone.now(),
        )
        payload = {"event": "qr_code.closed", "payload": {"qr_code": {"entity": {"id": "qr_closeme"}}}}
        body = json.dumps(payload)
        response = self.client.post(
            self._webhook_url(), data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=_sign(body),
        )
        self.assertEqual(response.status_code, 200)
        qr.refresh_from_db()
        self.assertEqual(qr.status, "expired")

    def test_reconciliation_path_when_already_paid_by_cash(self):
        """
        Real race: cashier takes cash and closes the order while the customer's
        UPI payment is still in flight. The webhook must not be dropped, and
        must not raise — it leaves an audit trail for manual refund instead.
        """
        order = self._make_order()
        process_payment(order, "cash", order.grand_total)  # closes the order first
        order.refresh_from_db()
        self.assertEqual(order.status, "closed")

        response = self._post_webhook(self._credited_payload(order, payment_id="pay_race"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get("reconciliation_needed"))
        # The money is real — it must be recorded, not silently dropped.
        razorpay_payment = Payment.objects.get(reference="pay_race")
        self.assertEqual(razorpay_payment.amount, order.grand_total)
        self.assertTrue(
            OrderEvent.objects.filter(
                order=order, event_type="razorpay_overpaid_reconciliation"
            ).exists()
        )

    def test_amount_mismatch_flagged_and_capped_to_remaining(self):
        """
        If the order changed after the QR was shown (e.g. item added), the
        webhook amount may exceed the current remaining balance. Record only
        what's actually owed and flag the mismatch for review.
        """
        order = self._make_order()
        overpay_paise = decimal_to_paise(order.grand_total) + 5000  # ₹50 more than owed
        response = self._post_webhook(
            self._credited_payload(order, payment_id="pay_mismatch", amount_paise=overpay_paise)
        )

        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get(reference="pay_mismatch")
        self.assertEqual(payment.amount, order.grand_total)  # capped, not the inflated amount
        self.assertTrue(
            OrderEvent.objects.filter(
                order=order, event_type="razorpay_amount_mismatch"
            ).exists()
        )


class RazorpayQRStatusViewTest(CounterBillingBase):

    def setUp(self):
        super().setUp()
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="razorpay_gateway", enabled=True)

    def test_returns_current_status(self):
        order = self._make_order()
        qr = RazorpayQRCode.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            qr_code_id="qr_status1", image_url="https://rzp.io/x.png",
            quoted_amount=order.grand_total, status="active", expires_at=timezone.now(),
        )
        response = self.client.get(reverse("razorpay-qr-status", args=[qr.qr_code_id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "active")


# ======================================================================
#  Moved from orders/tests/test_refunds.py
# ======================================================================

class RefundApprovalTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Test Outlet")

        self.owner = User.objects.create_user(
            username="owner", password="password", tenant=self.tenant, outlet=self.outlet, role="owner"
        )
        self.manager = User.objects.create_user(
            username="manager", password="password", tenant=self.tenant, outlet=self.outlet, role="manager"
        )
        self.waiter = User.objects.create_user(
            username="waiter", password="password", tenant=self.tenant, outlet=self.outlet, role="waiter"
        )

        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, created_by=self.owner, status="closed"
        )
        self.payment = Payment.objects.create(
            order=self.order, method="cash", amount=Decimal("1000.00"), created_by=self.owner
        )

    def test_waiter_cannot_request_refund(self):
        with self.assertRaises(PermissionDenied):
            process_refund(self.order, self.payment.id, 100, self.waiter)

    def test_manager_can_request_refund(self):
        refund = process_refund(self.order, self.payment.id, 100, self.manager)
        self.assertEqual(refund.status, "pending")
        self.assertEqual(refund.amount, Decimal("100.00"))

    def test_manager_cannot_approve_refund(self):
        refund = process_refund(self.order, self.payment.id, 100, self.manager)
        with self.assertRaises(PermissionDenied):
            approve_refund(refund.id, self.manager, self.tenant, self.outlet)

    def test_owner_can_approve_refund(self):
        refund = process_refund(self.order, self.payment.id, 100, self.manager)
        approve_refund(refund.id, self.owner, self.tenant, self.outlet)
        refund.refresh_from_db()
        self.assertEqual(refund.status, "approved")

    def test_manager_can_reject_refund(self):
        refund = process_refund(self.order, self.payment.id, 100, self.manager)
        reject_refund(refund.id, self.manager, self.tenant, self.outlet, reason="Mistake")
        refund.refresh_from_db()
        self.assertEqual(refund.status, "rejected")
        self.assertIn("Rejected: Mistake", refund.reason)

    def test_cannot_refund_more_than_payment(self):
        with self.assertRaises(ValidationError):
            process_refund(self.order, self.payment.id, 1100, self.manager)

    def test_cannot_double_refund_pending(self):
        process_refund(self.order, self.payment.id, 600, self.manager)
        # 600 is pending, so only 400 left. Requesting 500 should fail.
        with self.assertRaises(ValidationError):
            process_refund(self.order, self.payment.id, 500, self.manager)


# ======================================================================
#  Moved from orders/tests/test_critical.py::RefundDefaultStatusTest
# ======================================================================

class RefundDefaultStatusTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Refund Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Refund Cafe Main")
        self.user = User.objects.create_user(
            username="refund_owner", password="testpass123",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            table=table, created_by=self.user, status="open"
        )
        self.payment = Payment.objects.create(
            order=self.order, method="cash",
            amount=Decimal("100"), created_by=self.user
        )

    def test_refund_defaults_to_pending(self):
        refund = Refund.objects.create(
            payment=self.payment,
            order=self.order,
            amount=Decimal("50"),
            reason="Customer changed mind",
            refunded_by=self.user
        )
        self.assertEqual(refund.status, "pending",
                         "Refund must default to 'pending', not 'approved'")

    def test_refund_not_auto_approved(self):
        refund = Refund(
            payment=self.payment,
            order=self.order,
            amount=Decimal("10"),
            reason="Error",
            refunded_by=self.user
        )
        # Do NOT save — just verify the default field value
        self.assertNotEqual(refund.status, "approved")


# ======================================================================
#  Moved from orders/tests/test_security_fixes.py::RefundCrossTenantIDORTest
# ======================================================================

class RefundCrossTenantIDORTest(TestCase):
    """An owner in Tenant A must not be able to approve/reject Tenant B's refund."""

    def setUp(self):
        # Tenant A + owner
        self.tenant_a = Tenant.objects.create(name="A", slug="tenant-a")
        self.outlet_a = Outlet.objects.create(tenant=self.tenant_a, name="A-Main")
        self.owner_a = User.objects.create_user(
            username="owner_a", password="pw", role="owner",
            tenant=self.tenant_a, outlet=self.outlet_a,
        )
        # Tenant B + an order/payment/refund
        self.tenant_b = Tenant.objects.create(name="B", slug="tenant-b")
        self.outlet_b = Outlet.objects.create(tenant=self.tenant_b, name="B-Main")
        self.order_b = Order.objects.create(
            tenant=self.tenant_b, outlet=self.outlet_b, status="paid",
        )
        self.payment_b = Payment.objects.create(
            order=self.order_b, method="cash", amount=Decimal("300.00"),
        )
        self.refund_b = Refund.objects.create(
            payment=self.payment_b, order=self.order_b,
            amount=Decimal("100.00"), status="pending",
        )

    def test_owner_a_cannot_approve_tenant_b_refund(self):
        client = Client()
        client.force_login(self.owner_a)
        resp = client.post(
            reverse("approve-refund", args=[self.refund_b.id]),
            content_type="application/json",
        )
        # The view catches the scoped-lookup miss and returns a clean 400 —
        # crucially the refund must NOT be approved and no negative Payment made.
        self.refund_b.refresh_from_db()
        self.assertEqual(self.refund_b.status, "pending")
        self.assertFalse(
            Payment.objects.filter(order=self.order_b, method="refund").exists()
        )

    def test_owner_a_cannot_reject_tenant_b_refund(self):
        client = Client()
        client.force_login(self.owner_a)
        client.post(
            reverse("reject-refund", args=[self.refund_b.id]),
            data=json.dumps({"reason": "nope"}),
            content_type="application/json",
        )
        self.refund_b.refresh_from_db()
        self.assertEqual(self.refund_b.status, "pending")


# ======================================================================
#  New: closes a genuinely uncovered path found during the move.
#  orders/views/payment_views.py::refund_payment is the reverse-dependency
#  call site (orders -> payments), the cashier-facing "request a refund"
#  button on the bill screen. It had zero test coverage anywhere.
# ======================================================================

class RefundPaymentViewTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Refund Entry Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(
            username="refentry_mgr", password="pw",
            tenant=self.tenant, outlet=self.outlet, role="manager",
        )
        self.waiter = User.objects.create_user(
            username="refentry_waiter", password="pw",
            tenant=self.tenant, outlet=self.outlet, role="waiter",
        )
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            table=table, created_by=self.manager, status="closed",
        )
        self.payment = Payment.objects.create(
            order=self.order, method="cash", amount=Decimal("500.00"), created_by=self.manager,
        )

    def _post(self, user, amount=100, reason="Customer complaint"):
        client = Client()
        client.force_login(user)
        return client.post(
            reverse("refund-payment", args=[self.payment.id]),
            data=json.dumps({"amount": amount, "reason": reason}),
            content_type="application/json",
        )

    def test_manager_can_request_refund_via_http(self):
        resp = self._post(self.manager)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            Refund.objects.filter(payment=self.payment, order=self.order, status="pending").exists()
        )

    def test_waiter_cannot_request_refund_via_http(self):
        resp = self._post(self.waiter)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Refund.objects.filter(payment=self.payment).exists())

    def test_missing_reason_rejected(self):
        client = Client()
        client.force_login(self.manager)
        resp = client.post(
            reverse("refund-payment", args=[self.payment.id]),
            data=json.dumps({"amount": 100}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Refund.objects.filter(payment=self.payment).exists())
