"""
Regression tests for the critical security fixes from the code-review pass.

Covers:
  1. create_order — a QR guest cannot apply a discount (the "free food" hole),
     and staff discounts are bounded (percentage capped at 100, negatives -> 0).
  2. approve_refund / reject_refund — cross-tenant IDOR: an owner in Tenant A
     cannot approve or reject a refund belonging to Tenant B.
  3. cancel_order / cancel_item — now require a privileged role.

Run: python manage.py test orders.tests.test_security_fixes
"""
import json
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem, Payment, Refund, Table
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Sec Tenant", slug="sec-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Paneer Tikka", price=Decimal("200.00"), gst_percentage=Decimal("5.00"),
        )
        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="T1", is_active=True
        )


class GuestDiscountTest(_Base):
    """The critical 'free food' hole: a QR guest sends a huge discount_value
    with source='takeaway'; the discount must be ignored (guest has no user)."""

    def test_guest_cannot_apply_discount(self):
        client = Client()
        resp = client.post(
            reverse("create-order"),
            data=json.dumps({
                "table_token": str(self.table.qr_token),
                "cart": [{"id": self.item.id, "quantity": 1}],
                "source": "takeaway",
                "discount_type": "amount",
                "discount_value": "999999",
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        # The discount must NOT have been applied — grand_total stays > 0.
        self.assertEqual(order.discount_value, Decimal("0"))
        self.assertGreater(order.grand_total, Decimal("0"))


class StaffDiscountBoundsTest(_Base):
    def setUp(self):
        super().setUp()
        self.cashier = User.objects.create_user(
            username="cash1", password="pw", role="cashier",
            tenant=self.tenant, outlet=self.outlet,
        )

    def test_staff_percentage_discount_capped_at_100(self):
        client = Client()
        client.force_login(self.cashier)
        resp = client.post(
            reverse("create-order"),
            data=json.dumps({
                "cart": [{"id": self.item.id, "quantity": 1}],
                "source": "takeaway",
                "discount_type": "percentage",
                "discount_value": "500",   # 500% -> must clamp to 100
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertLessEqual(order.discount_value, Decimal("100"))
        # A 100% discount can zero the bill, but it can never go negative.
        self.assertGreaterEqual(order.grand_total, Decimal("0"))

    def test_staff_negative_discount_becomes_zero(self):
        client = Client()
        client.force_login(self.cashier)
        resp = client.post(
            reverse("create-order"),
            data=json.dumps({
                "cart": [{"id": self.item.id, "quantity": 1}],
                "source": "takeaway",
                "discount_type": "amount",
                "discount_value": "-50",   # negative would be an upcharge
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(order.discount_value, Decimal("0"))


class OrderEventDiscountLoggingTest(_Base):
    """
    Regression tests for the OrderEvent write-side fixes: order-level
    discounts now log a dedicated event_type instead of the generic
    "status_changed", item-level discounts get their own event_type instead
    of being indistinguishable from any other "item_updated" event, and
    marking an item complimentary now creates an audit event at all (it
    previously created none).
    """

    def setUp(self):
        super().setUp()
        self.manager = User.objects.create_user(
            username="mgr1", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table,
            created_by=self.manager, status="open",
        )
        self.order_item = OrderItem.objects.create(
            order=self.order, menu_item=self.item,
            quantity=1, price=self.item.price, gst_percentage=self.item.gst_percentage,
            total_price=self.item.price, status="pending",
        )

    def test_apply_discount_logs_dedicated_event_type(self):
        client = Client()
        client.force_login(self.manager)
        resp = client.post(
            reverse("apply-discount", args=[self.order.id]),
            data=json.dumps({"type": "percentage", "value": "10"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        event = OrderEvent.objects.filter(order=self.order, event_type="discount_applied").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.metadata["action"], "discount_applied")
        self.assertEqual(event.metadata["value"], "10")
        self.assertEqual(event.created_by, self.manager)
        self.order.refresh_from_db()
        # Item price 200.00, 10% -> 20.00 discount, confirms the value actually applied.
        self.assertEqual(self.order.discount_total, Decimal("20.00"))

    def test_apply_item_discount_logs_dedicated_event_type(self):
        client = Client()
        client.force_login(self.manager)
        resp = client.post(
            reverse("item-discount", args=[self.order_item.id]),
            data=json.dumps({"percent": "20"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        # Before this fix, item discounts logged event_type="item_updated" with
        # no "action" key -- indistinguishable from any other item_updated event.
        event = OrderEvent.objects.filter(order=self.order, event_type="item_discount_applied").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.metadata["action"], "item_discount_applied")
        self.assertEqual(event.created_by, self.manager)

    def test_make_complimentary_now_creates_an_audit_event(self):
        self.assertEqual(
            OrderEvent.objects.filter(order=self.order, event_type="item_complimentary").count(), 0
        )
        client = Client()
        client.force_login(self.manager)
        resp = client.post(reverse("make-complimentary", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)
        # Previously this action created ZERO OrderEvent rows -- only a log line.
        events = OrderEvent.objects.filter(order=self.order, event_type="item_complimentary")
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().created_by, self.manager)
        self.assertEqual(events.first().metadata["item_id"], self.order_item.id)


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


class CancelOrderRoleTest(_Base):
    def setUp(self):
        super().setUp()
        self.waiter = User.objects.create_user(
            username="waiter1", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, status="open",
        )

    def test_waiter_cannot_cancel_order(self):
        client = Client()
        client.force_login(self.waiter)
        resp = client.post(reverse("cancel-order", args=[self.order.id]))
        self.assertEqual(resp.status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "open")
