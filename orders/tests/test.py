# orders/tests/test.py
#
# BUGS FIXED IN THIS FILE
# ========================
# Bug 1 — Wrong URL
#   Old:  self.client.get(f'/orders/{self.order.id}/billing/')
#   Why:  No such URL pattern exists. orders/urls.py defines path("billing/", ...)
#         and is included at root with no prefix, so the real URL is /billing/.
#   Fix:  Use reverse('billing-view') + f'?table={self.table.id}'
#
# Bug 2 — Missing query param
#   Old:  URL had no ?table= param
#   Why:  billing_view() does table_id = request.GET.get("table") — it looks up
#         the order via table, not via order ID in the URL. Without ?table=, the
#         view returns the blank POS page (HTTP 200 with order=None), which means
#         the lock check is never reached.
#   Fix:  Always pass ?table={self.table.id} on both test requests.
#
# Bug 3 — Order must have status "open" or "billing"
#   Old:  Order.objects.create(...) — no status field set
#   Why:  billing_view filters: status__in=["open", "billing"]. Default status may
#         not be in this list, so order would come back None again.
#   Fix:  Explicitly set status="open" in setUp.
#
# Bug 4 — Lock set on wrong model
#   Old:  self.order.locked_by = self.user1 / self.order.save()
#   Why:  Order has NO locked_by field. The lock system lives entirely in the
#         separate OrderLock model (OneToOneField → Order, related_name="lock").
#         lock_order() in order_lock_service.py reads order.lock and checks
#         lock.locked_by — it never looks at a field on Order itself.
#   Fix:  Create an OrderLock instance directly with expires_at in the future.

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from orders.models import Order, OrderLock, Table
from tenants.models import Outlet, Tenant

User = get_user_model()


class OrderLockTestCase(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test", slug="test")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Outlet")

        self.user1 = User.objects.create_user(
            username='user1',
            password='pass123',
            tenant=self.tenant,
            outlet=self.outlet,
        )
        self.user2 = User.objects.create_user(
            username='user2',
            password='pass123',
            tenant=self.tenant,
            outlet=self.outlet,
        )

        self.table = Table.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Table 1",
        )

        # Bug 3 fix: status must be "open" or "billing" so billing_view
        # can find the order via its filter: status__in=["open", "billing"]
        self.order = Order.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            table=self.table,
            created_by=self.user1,
            status="open",
        )

        self.client = Client()

    # ------------------------------------------------------------------
    # Helper: build the billing URL with the correct table query param
    # ------------------------------------------------------------------
    def _billing_url(self):
        """
        billing_view is registered as path("billing/", ..., name="billing-view")
        in orders/urls.py, which is included at root with no prefix.
        The view looks up the order via ?table=<id>, not via URL path param.
        """
        return reverse('billing-view') + f'?table={self.table.id}'

    # ------------------------------------------------------------------
    # Test 1 — creator gets HTTP 200 on an unlocked order
    # ------------------------------------------------------------------
    def test_order_lock_fetched_before_check(self):
        """
        The order is unlocked. user1 (creator) opens the billing page.
        lock_order() will create a fresh OrderLock for user1 and return
        (True, user1), so billing.html is rendered → HTTP 200.
        """
        self.client.login(username='user1', password='pass123')

        # Bug 1 fix: /billing/?table=<id>  (not /orders/<id>/billing/)
        # Bug 2 fix: ?table= param included so view can find the order
        response = self.client.get(self._billing_url())

        self.assertEqual(response.status_code, 200)

    # ------------------------------------------------------------------
    # Test 2 — other user is blocked when order is locked by user1
    # ------------------------------------------------------------------
    def test_order_lock_prevents_other_user(self):
        """
        user1 holds an active lock. user2 tries to open the same order.
        lock_order() sees an unexpired lock owned by a different user and
        returns (False, user1), so order_locked.html is rendered.
        That template contains the text "Order Currently Locked" → assert passes.
        """
        # Bug 4 fix: create an OrderLock row — Order has no locked_by field.
        # The lock service reads order.lock (the OneToOneField reverse accessor)
        # and checks lock.locked_by and lock.expires_at.
        OrderLock.objects.create(
            order=self.order,
            locked_by=self.user1,
            expires_at=timezone.now() + timedelta(seconds=300),
        )

        self.client.login(username='user2', password='pass123')

        # Same URL fixes as Test 1
        response = self.client.get(self._billing_url())

        # order_locked.html renders "Order Currently Locked" — "locked" is present
        self.assertIn("locked", response.content.decode().lower())
