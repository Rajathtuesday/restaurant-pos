"""
Tests for the outlet-wide "Counter / Walk-in" QR (Outlet.qr_token).

Background: create_order's QR-guest path only ever resolved a Table by
qr_token, so a QSR/cafe outlet with no seating (nothing to hang a
per-table QR on) had no way to accept QR orders at all. Outlet.qr_token
gives such outlets one static QR for the whole counter; orders placed
through it get table=None, same as any other walk-in order.

The one thing that needed real care: a tableless guest's order_id can't be
trusted the way a table-scoped guest's can, because the counter QR token
is shared by every customer (unlike a table's, which is scoped to whoever
is physically sitting there) -- so a stored order_id from an earlier visit
must never let a guest silently attach items to someone else's open order.

Run: python manage.py test orders.tests.test_counter_qr
"""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Counter Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Snacks"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Samosa", price=Decimal("30.00"),
        )

    def _place(self, item, qty=1, order_id=None):
        payload = {
            "table_token": str(self.outlet.qr_token),
            "cart": [{"id": item.id, "quantity": qty}],
            "source": "web",
        }
        if order_id:
            payload["order_id"] = order_id
        return self.client.post(
            reverse("create-order"),
            data=json.dumps(payload),
            content_type="application/json",
        )


class CounterOrderCreationTest(_Base):
    def test_outlet_qr_token_creates_tableless_order(self):
        resp = self._place(self.item, qty=2)
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertIsNone(order.table)
        self.assertEqual(order.tenant_id, self.tenant.id)
        self.assertEqual(order.outlet_id, self.outlet.id)

    def test_cafe_tenant_still_gets_a_token_for_counter_order(self):
        resp = self._place(self.item)
        self.assertEqual(resp.status_code, 200)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertTrue(hasattr(order, "token"))
        self.assertIsNotNone(order.token.token_number)

    def test_unknown_qr_token_404s(self):
        import uuid
        payload = {
            "table_token": str(uuid.uuid4()),
            "cart": [{"id": self.item.id, "quantity": 1}],
            "source": "web",
        }
        resp = self.client.post(
            reverse("create-order"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)


class CounterGuestReorderIsolationTest(_Base):
    """
    A tableless guest passing a stale/guessed order_id must never merge
    into that order -- there's no physical-table boundary to verify
    ownership with on a shared counter QR, so every submission from a
    tableless guest becomes its own fresh order instead.
    """

    def test_passing_someone_elses_order_id_creates_a_fresh_order_instead(self):
        first = self._place(self.item, qty=1)
        first_order_id = first.json()["order_id"]

        second = self._place(self.item, qty=1, order_id=first_order_id)
        self.assertEqual(second.status_code, 200)
        second_order_id = second.json()["order_id"]

        self.assertNotEqual(first_order_id, second_order_id)
        first_order = Order.objects.get(id=first_order_id)
        self.assertEqual(first_order.items.count(), 1)


class ApproveGuestOrderItemsTest(_Base):
    """
    Any guest-placed QR order (table or counter) creates its items with
    status="review" (orders/services/order_service.py: "review" if user is
    None else "pending") -- they need staff sign-off before they count
    toward the bill or reach the kitchen. The Token Billing page (staff
    cashier UI for QSR/cafe orders) had no way at all to give that
    sign-off -- items just sat there forever showing a raw "review" status
    with no action to approve or send them.

    Fixed with approve_item (orders/views/order_views.py), the singular
    sibling of the existing bulk approve_items (which the fine-dining floor
    plan uses) -- Token Billing acts per item, not all-or-nothing, so one
    bad item can be rejected via the existing cancel_item without forcing
    every other item on the order through too.
    """

    def setUp(self):
        super().setUp()
        self.cashier = User.objects.create_user(
            username="cashier1", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="cashier",
        )
        self.item2 = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Cold Coffee", price=Decimal("80.00"),
        )

    def _client(self):
        client = self.client_class()
        client.login(username="cashier1", password="pw")
        return client

    def test_counter_qr_order_items_start_as_review(self):
        resp = self._place(self.item, qty=1)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(list(order.items.values_list("status", flat=True)), ["review"])

    def test_staff_can_bulk_approve_and_it_sends_to_kitchen(self):
        # approve-items (bulk) is still used by the fine-dining floor plan --
        # unchanged, still needs to keep working.
        resp = self._place(self.item, qty=1)
        order_id = resp.json()["order_id"]

        approve_resp = self._client().post(reverse("approve-items", args=[order_id]))
        self.assertEqual(approve_resp.status_code, 200)
        self.assertEqual(approve_resp.json()["count"], 1)

        order = Order.objects.get(id=order_id)
        item = order.items.first()
        # approve_items flips review -> pending, then immediately creates a
        # KOT for it, which itself advances pending -> sent -- so by the
        # time this returns, the item isn't just "approved", it's already
        # in the kitchen's queue.
        self.assertEqual(item.status, "sent")

    def test_bulk_approving_with_no_review_items_404s(self):
        resp = self._client().post(reverse("approve-items", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_staff_can_approve_one_item_leaving_the_other_untouched(self):
        # Token Billing acts per item, not all-or-nothing -- a two-item
        # counter order, approve only one.
        payload = {
            "table_token": str(self.outlet.qr_token),
            "cart": [
                {"id": self.item.id, "quantity": 1},
                {"id": self.item2.id, "quantity": 1},
            ],
            "source": "web",
        }
        resp = self.client.post(
            reverse("create-order"), data=json.dumps(payload), content_type="application/json",
        )
        order = Order.objects.get(id=resp.json()["order_id"])
        review_items = list(order.items.all())
        self.assertEqual(len(review_items), 2)
        approved_item, other_item = review_items

        approve_resp = self._client().post(reverse("approve-item", args=[approved_item.id]))
        self.assertEqual(approve_resp.status_code, 200)
        self.assertTrue(approve_resp.json()["success"])

        approved_item.refresh_from_db()
        other_item.refresh_from_db()
        self.assertEqual(approved_item.status, "sent")
        self.assertEqual(other_item.status, "review")

    def test_approving_an_already_approved_item_errors(self):
        resp = self._place(self.item, qty=1)
        order = Order.objects.get(id=resp.json()["order_id"])
        item = order.items.first()

        client = self._client()
        first = client.post(reverse("approve-item", args=[item.id]))
        self.assertEqual(first.status_code, 200)

        second = client.post(reverse("approve-item", args=[item.id]))
        self.assertEqual(second.status_code, 400)

    def test_staff_can_reject_a_single_item_via_cancel_item(self):
        resp = self._place(self.item, qty=1)
        order = Order.objects.get(id=resp.json()["order_id"])
        item = order.items.first()

        reject_resp = self._client().post(reverse("cancel-item", args=[item.id]))
        self.assertEqual(reject_resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, "voided")

    def test_approving_a_nonexistent_item_404s(self):
        resp = self._client().post(reverse("approve-item", args=[999999]))
        self.assertEqual(resp.status_code, 404)
