# orders/tests/test_refunds.py
from decimal import Decimal
from django.test import TestCase, Client
from django.core.exceptions import PermissionDenied, ValidationError
from accounts.models import User
from orders.models import Order, Payment, Refund, Table
from orders.services.refund_service import process_refund, approve_refund, reject_refund
from tenants.models import Tenant, Outlet

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
            approve_refund(refund.id, self.manager)

    def test_owner_can_approve_refund(self):
        refund = process_refund(self.order, self.payment.id, 100, self.manager)
        approve_refund(refund.id, self.owner)
        refund.refresh_from_db()
        self.assertEqual(refund.status, "approved")

    def test_manager_can_reject_refund(self):
        refund = process_refund(self.order, self.payment.id, 100, self.manager)
        reject_refund(refund.id, self.manager, reason="Mistake")
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
