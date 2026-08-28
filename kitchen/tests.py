# kitchen/tests.py
"""
Bug: kitchen/views.py (then orders/views/kitchen_views.py) had zero
@role_required gates on any of its 8 endpoints (every other file under
orders/views/ consistently uses role_required) - any authenticated staff
member of the tenant, including a waiter, could hit /kitchen/, mark items
preparing/ready, and bump whole KOTs, none of which are waiter actions.

Fix: role_required added per-endpoint, matching who actually performs each
action:
  - send_to_kitchen        -> front-of-house (owner/manager/cashier/waiter)
  - kitchen_view/data,
    start_preparing,
    mark_ready, bump_kot    -> kitchen staff only (owner/manager/chef/kitchen)
  - serve_item,
    send_kitchen_message    -> anyone who can be at a table (adds waiter/cashier)

Also includes (moved from orders/tests/, Phase 3 of the orders app split):
  - TestKOTBatchIndexes (orders/tests/test_schema_review.py)
  - KOTCounterTenantIsolationTest (orders/tests/test_critical.py)

Run: python manage.py test kitchen
"""
import json
import sys
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from kitchen.services.kot_service import create_kot
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Kitchen Perm Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

        self.owner = self._user("owner1", "owner")
        self.manager = self._user("mgr1", "manager")
        self.cashier = self._user("cash1", "cashier")
        self.waiter = self._user("wait1", "waiter")
        self.chef = self._user("chef1", "chef")
        self.kitchen = self._user("kit1", "kitchen")

        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Burger", price=100,
        )
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, status="open",
        )
        self.order_item = OrderItem.objects.create(
            order=self.order,
            menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="pending",
        )

    def _user(self, username, role):
        return User.objects.create_user(
            username=username, password="pw", tenant=self.tenant,
            outlet=self.outlet, role=role,
        )

    def _login(self, user):
        client = Client()
        client.login(username=user.username, password="pw")
        return client


class SendToKitchenPermissionTest(_Base):
    def test_waiter_can_send_order_to_kitchen(self):
        resp = self._login(self.waiter).post(reverse("send-to-kitchen", args=[self.order.id]))
        self.assertEqual(resp.status_code, 200)

    def test_chef_cannot_send_order_to_kitchen(self):
        # Sending TO the kitchen is a front-of-house action, not something
        # kitchen staff themselves do.
        resp = self._login(self.chef).post(reverse("send-to-kitchen", args=[self.order.id]))
        self.assertEqual(resp.status_code, 403)


class KitchenStateActionsPermissionTest(_Base):
    def setUp(self):
        super().setUp()
        create_kot(self.owner, self.order)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "sent")

    def test_waiter_cannot_view_kitchen_dashboard(self):
        resp = self._login(self.waiter).get(reverse("kitchen-view"))
        self.assertEqual(resp.status_code, 403)

    def test_waiter_cannot_view_kitchen_data(self):
        resp = self._login(self.waiter).get(reverse("kitchen-data"))
        self.assertEqual(resp.status_code, 403)

    def test_waiter_cannot_start_preparing_item(self):
        resp = self._login(self.waiter).post(reverse("item-start", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 403)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "sent")  # unchanged

    def test_cashier_cannot_mark_item_ready(self):
        resp = self._login(self.cashier).post(reverse("mark-ready", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 403)

    def test_chef_can_start_preparing_and_mark_ready(self):
        resp = self._login(self.chef).post(reverse("item-start", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)
        resp = self._login(self.chef).post(reverse("mark-ready", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "ready")

    def test_marking_ready_creates_kitchen_message_not_a_dead_notification(self):
        """
        set_item_ready used to also write a Notification(type="order_ready")
        for the exact same event the KitchenMessage below already covers --
        a duplicate write to a channel nothing ever displayed to a user
        (the poller that fetched it never rendered it). Removed as dead
        code, not replaced with anything, since KitchenMessage already does
        the real job.
        """
        from kitchen.models import KitchenMessage
        from notifications.models import Notification

        self._login(self.chef).post(reverse("item-start", args=[self.order_item.id]))
        self._login(self.chef).post(reverse("mark-ready", args=[self.order_item.id]))

        self.assertTrue(
            KitchenMessage.objects.filter(order=self.order).exists()
        )
        self.assertFalse(
            Notification.objects.filter(tenant=self.tenant, type="order_ready").exists()
        )

    def test_kitchen_role_can_view_dashboard_and_bump_kot(self):
        resp = self._login(self.kitchen).get(reverse("kitchen-view"))
        self.assertEqual(resp.status_code, 200)

    def test_manager_can_do_everything_kitchen_can(self):
        resp = self._login(self.manager).post(reverse("item-start", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)

    def test_bump_kot_logs_a_warning_for_items_it_cannot_bump(self):
        # bump_kot silently absorbed per-item failures with zero trace of
        # which item failed or why - only the lower "bumped" count hinted
        # something went wrong. A "sent" item can't skip straight to
        # "ready" (set_item_ready requires "preparing"), so mixing a sent
        # item into an otherwise-preparing KOT is a realistic trigger.
        self.order_item.status = "preparing"
        self.order_item.save(update_fields=["status"])

        second_item = OrderItem.objects.create(
            order=self.order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="sent",
        )
        second_item.kot = self.order_item.kot
        second_item.save(update_fields=["kot"])

        with self.assertLogs("pos.orders", level="WARNING") as cm:
            resp = self._login(self.chef).post(
                reverse("bump-kot", args=[self.order_item.kot_id])
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["bumped"], 1)  # only the preparing item
        self.assertTrue(any("bump_kot" in msg for msg in cm.output))


class KitchenDataVisibleAfterPaymentTest(_Base):
    """
    get_kitchen_data used to filter to order__status__in=["open","billing"]
    -- correct for fine dining, where kitchen prep always happens before
    the bill is paid. Token (QSR/cafe) orders are the opposite: pay_order
    fires the KOT and closes the order in the same request (see
    payment_views.py's _auto_kot), so by the time anyone loads the kitchen
    screen the order is already "paid"/"closed" even though nothing has
    been prepared yet -- the old filter excluded exactly the orders the
    kitchen most needed to see.
    """

    def setUp(self):
        super().setUp()
        create_kot(self.owner, self.order)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "sent")

    def _kitchen_data(self):
        resp = self._login(self.chef).get(reverse("kitchen-data"))
        return resp.json()["kots"]

    def test_kot_visible_when_order_is_paid(self):
        self.order.status = "paid"
        self.order.save(update_fields=["status"])
        data = self._kitchen_data()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["order_id"], self.order.id)

    def test_kot_visible_when_order_is_closed(self):
        self.order.status = "closed"
        self.order.save(update_fields=["status"])
        data = self._kitchen_data()
        self.assertEqual(len(data), 1)

    def test_kot_hidden_when_order_is_cancelled(self):
        self.order.status = "cancelled"
        self.order.save(update_fields=["status"])
        data = self._kitchen_data()
        self.assertEqual(data, [])

    def test_kot_hidden_once_item_served_regardless_of_order_status(self):
        self.order.status = "paid"
        self.order.save(update_fields=["status"])
        self.order_item.status = "served"
        self.order_item.save(update_fields=["status"])
        data = self._kitchen_data()
        self.assertEqual(data, [])


class ServeAndMessagePermissionTest(_Base):
    def setUp(self):
        super().setUp()
        create_kot(self.owner, self.order)
        self.order_item.status = "ready"
        self.order_item.save(update_fields=["status"])

    def test_waiter_can_serve_item(self):
        resp = self._login(self.waiter).post(reverse("serve-item", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)

    def test_cashier_can_serve_item(self):
        resp = self._login(self.cashier).post(reverse("serve-item", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)

    def test_waiter_can_send_kitchen_message(self):
        resp = self._login(self.waiter).post(
            reverse("send-kitchen-message", args=[self.order.id]),
            data=json.dumps({"message": "Guest allergic to peanuts"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)


class ClearAllKitchenItemsTest(_Base):
    """
    Clear All is a blunt cleanup tool for stale/orphaned tickets (old test
    data, a KOT nobody ever finished tapping through), not a normal
    service action -- restricted to owner/manager, tighter than the
    per-item actions chef/kitchen can also do.
    """

    def setUp(self):
        super().setUp()
        create_kot(self.owner, self.order)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "sent")

    def test_chef_cannot_clear_all(self):
        resp = self._login(self.chef).post(reverse("clear-all-kitchen"))
        self.assertEqual(resp.status_code, 403)

    def test_kitchen_role_cannot_clear_all(self):
        resp = self._login(self.kitchen).post(reverse("clear-all-kitchen"))
        self.assertEqual(resp.status_code, 403)

    def test_owner_can_clear_all(self):
        resp = self._login(self.owner).post(reverse("clear-all-kitchen"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 1)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "served")

    def test_manager_can_clear_all(self):
        resp = self._login(self.manager).post(reverse("clear-all-kitchen"))
        self.assertEqual(resp.status_code, 200)

    def test_clears_items_across_multiple_orders_and_stations(self):
        other_table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T2")
        other_order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=other_table, status="paid",
        )
        other_item = OrderItem.objects.create(
            order=other_order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="pending",
        )
        create_kot(self.owner, other_order)
        other_item.refresh_from_db()
        self.assertEqual(other_item.status, "sent")

        resp = self._login(self.owner).post(reverse("clear-all-kitchen"))
        self.assertEqual(resp.json()["count"], 2)
        self.order_item.refresh_from_db()
        other_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "served")
        self.assertEqual(other_item.status, "served")

    def test_does_not_touch_a_cancelled_orders_items(self):
        self.order.status = "cancelled"
        self.order.save(update_fields=["status"])
        resp = self._login(self.owner).post(reverse("clear-all-kitchen"))
        self.assertEqual(resp.json()["count"], 0)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "sent")

    def test_does_not_touch_another_outlets_items(self):
        other_outlet = Outlet.objects.create(tenant=self.tenant, name="Branch 2")
        other_order = Order.objects.create(
            tenant=self.tenant, outlet=other_outlet, status="open",
        )
        other_item = OrderItem.objects.create(
            order=other_order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="sent",
        )
        resp = self._login(self.owner).post(reverse("clear-all-kitchen"))
        self.assertEqual(resp.json()["count"], 1)  # only self.order_item, not other_outlet's
        other_item.refresh_from_db()
        self.assertEqual(other_item.status, "sent")


class TokenReadyOnKitchenReadyTest(TestCase):
    """
    QSR/cafe outlets that run a kitchen display (franchise and cafe both
    have kitchen_display ON by default, alongside token_system) had no link
    at all between an item going 'ready' on the KDS and TokenOrder.ready_at
    -- the pickup display board and the guest's own phone never lit up
    unless staff ALSO went to the Token Dashboard and tapped "Mark Ready"
    separately. set_item_ready now sets ready_at itself once every item on
    the order is ready/served/voided.
    """

    def setUp(self):
        from datetime import date
        from tokens.models import TokenOrder

        self.tenant = Tenant.objects.create(name="KDS Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.chef = User.objects.create_user(
            username="chef1", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="chef",
        )
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Burger", price=100,
        )
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=None, status="open", source="counter",
        )
        self.token = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=self.order,
            token_number=1, date=date.today(), is_online=False,
        )

    def _login(self, user):
        client = Client()
        client.login(username=user.username, password="pw")
        return client

    def test_last_item_ready_sets_token_ready_at(self):
        item1 = OrderItem.objects.create(
            order=self.order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="preparing",
        )
        item2 = OrderItem.objects.create(
            order=self.order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="preparing",
        )
        client = self._login(self.chef)

        client.post(reverse("mark-ready", args=[item1.id]))
        self.token.refresh_from_db()
        self.assertIsNone(self.token.ready_at, "still one item preparing -- not ready yet")

        client.post(reverse("mark-ready", args=[item2.id]))
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.ready_at)

    def test_bump_kot_sets_token_ready_at(self):
        OrderItem.objects.create(
            order=self.order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="pending",
        )
        create_kot(self.chef, self.order)
        item = self.order.items.first()
        item.status = "preparing"
        item.save(update_fields=["status"])

        resp = self._login(self.chef).post(reverse("bump-kot", args=[item.kot_id]))
        self.assertEqual(resp.status_code, 200)
        self.token.refresh_from_db()
        self.assertIsNotNone(self.token.ready_at)

    def test_does_not_overwrite_an_already_set_ready_at(self):
        from datetime import timedelta
        from django.utils import timezone
        original = timezone.now() - timedelta(minutes=5)
        self.token.ready_at = original
        self.token.save(update_fields=["ready_at"])

        item = OrderItem.objects.create(
            order=self.order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="preparing",
        )
        self._login(self.chef).post(reverse("mark-ready", args=[item.id]))
        self.token.refresh_from_db()
        self.assertEqual(self.token.ready_at, original)


class TokenOrderClearsFromKitchenDisplayTest(TestCase):
    """
    A counter/token order has no waiter-delivery step the way dine-in
    does, so nothing was ever moving its items past "ready" -- they sat
    on the Kitchen Display forever, even once the customer had physically
    collected the order. Two fixes, covering the normal case and the
    forgot-to-tap case:

    1. mark_token_collected ("Picked Up") now marks the order's ready
       items "served" -- the counter equivalent of a waiter serving a
       table -- which the KDS already hides via its existing
       exclude(status__in=["served","voided"]).
    2. get_kitchen_data now also drops a token order once it's been
       ready for more than 5 minutes, regardless of whether "Picked Up"
       was ever tapped -- a safety net for the case staff forget, same
       cutoff the guest-facing pickup board already uses. Dine-in/table
       orders are NOT subject to this timeout -- only orders with a
       TokenOrder attached.
    """

    def setUp(self):
        from datetime import date
        from tokens.models import TokenOrder

        self.tenant = Tenant.objects.create(name="KDS Clear Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.cashier = User.objects.create_user(
            username="cashier1", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="cashier",
        )
        self.chef = User.objects.create_user(
            username="chef1", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="chef",
        )
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Burger", price=100,
        )
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=None, status="paid", source="counter",
        )
        self.order_item = OrderItem.objects.create(
            order=self.order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="pending",
        )
        self.token = TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=self.order,
            token_number=1, date=date.today(), is_online=False,
        )
        create_kot(self.chef, self.order)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "sent")
        # Simulate the kitchen having marked it ready.
        self.order_item.status = "ready"
        self.order_item.save(update_fields=["status"])

    def _login(self, user):
        client = Client()
        client.login(username=user.username, password="pw")
        return client

    def _kitchen_data(self):
        resp = self._login(self.chef).get(reverse("kitchen-data"))
        return resp.json()["kots"]

    def test_picked_up_marks_ready_items_served_and_clears_kds(self):
        resp = self._login(self.cashier).post(reverse("mark-token-collected", args=[self.token.id]))
        self.assertEqual(resp.status_code, 200)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "served")

    def test_picked_up_leaves_a_still_preparing_item_alone(self):
        self.order_item.status = "preparing"
        self.order_item.save(update_fields=["status"])
        self._login(self.cashier).post(reverse("mark-token-collected", args=[self.token.id]))
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "preparing")

    def test_stale_ready_token_order_clears_from_kds_even_without_pickup_tap(self):
        from datetime import timedelta
        from django.utils import timezone
        self.token.ready_at = timezone.now() - timedelta(minutes=6)
        self.token.save(update_fields=["ready_at"])
        self.assertEqual(self._kitchen_data(), [])

    def test_recently_ready_token_order_still_shows_on_kds(self):
        from datetime import timedelta
        from django.utils import timezone
        self.token.ready_at = timezone.now() - timedelta(minutes=2)
        self.token.save(update_fields=["ready_at"])
        data = self._kitchen_data()
        self.assertEqual(len(data), 1)

    def test_dine_in_order_never_auto_clears_on_a_timer(self):
        # No TokenOrder attached -- the 5-minute stale rule must not apply
        # to a plain dine-in ticket just because time passed.
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=table, status="open",
        )
        item = OrderItem.objects.create(
            order=order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="pending",
        )
        create_kot(self.chef, order)
        item.refresh_from_db()
        self.assertEqual(item.status, "sent")

        data = self._kitchen_data()
        order_ids = {kot["order_id"] for kot in data}
        self.assertIn(order.id, order_ids)


class DineInOrderClearsFromKitchenOnCloseTest(TestCase):
    """
    Dine-in equivalent of TokenOrderClearsFromKitchenDisplayTest above.
    A dine-in order had no path to "served" other than a waiter explicitly
    tapping Serve on a different screen -- closing the bill (normal
    payment, a complimentary order, or a manager's payment-gate bypass)
    never touched OrderItem.status, so a "ready" item on a paid/closed
    order sat on the Kitchen Display forever. mark_ready_items_served
    (orders/services/payment_service.py) is now called from all three
    closing paths, mirroring what mark_token_collected already does for
    counter/token orders -- but must stay a no-op for token orders, since
    those pay BEFORE cooking and already have their own correct handling.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="KDS Dine-in Close Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="owner1", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="owner",
        )
        self.manager = User.objects.create_user(
            username="mgr1", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="manager",
        )
        self.chef = User.objects.create_user(
            username="chef1", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="chef",
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Burger", price=100,
        )

    def _order(self, grand_total, item_status):
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table,
            status="open", grand_total=grand_total,
        )
        item = OrderItem.objects.create(
            order=order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="pending",
        )
        # Route through create_kot so the item is attached to a KOTBatch --
        # get_kitchen_data only ever looks at items via kot.items, so an
        # OrderItem created with a status but no KOT is invisible to it
        # regardless of status, which isn't what these tests are checking.
        create_kot(self.chef, order)
        item.refresh_from_db()
        item.status = item_status
        item.save(update_fields=["status"])
        return order, item

    def _login(self, user):
        client = Client()
        client.login(username=user.username, password="pw")
        return client

    def _kitchen_data(self, order):
        resp = self._login(self.chef).get(reverse("kitchen-data"))
        data = resp.json()["kots"]
        return [kot for kot in data if kot["order_id"] == order.id]

    def test_ready_item_clears_after_normal_payment_closes_order(self):
        from orders.services.payment_service import process_payment
        order, item = self._order(grand_total=105, item_status="ready")
        process_payment(order, "cash", 105, user=self.owner)
        item.refresh_from_db()
        self.assertEqual(item.status, "served")
        self.assertEqual(self._kitchen_data(order), [])

    def test_still_cooking_item_is_left_alone_when_order_closes(self):
        from orders.services.payment_service import process_payment
        order, item = self._order(grand_total=105, item_status="preparing")
        process_payment(order, "cash", 105, user=self.owner)
        item.refresh_from_db()
        self.assertEqual(item.status, "preparing")
        self.assertNotEqual(self._kitchen_data(order), [])

    def test_complimentary_order_clears_ready_item(self):
        from setup.models import PaymentConfig
        PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet)
        order, item = self._order(grand_total=0, item_status="ready")
        resp = self._login(self.owner).post(
            reverse("pay-order", args=[order.id]),
            data=json.dumps({"method": "cash", "amount": 0}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, "served")

    def test_manager_bypass_clears_ready_item(self):
        order, item = self._order(grand_total=105, item_status="ready")
        resp = self._login(self.manager).post(reverse("log-bypass", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, "served")

    def test_manager_bypass_does_not_depend_on_pytz(self):
        """
        log_bypass's non-owner daily-limit check used to import pytz, which
        isn't in requirements.txt -- it happened to be present in local dev
        venvs as a leftover/transitive package, so this passed locally but
        threw ModuleNotFoundError (swallowed into a 500 by log_bypass's own
        except Exception:) the moment a real CI install first exercised this
        exact manager-role path. Fixed by switching to the stdlib zoneinfo,
        already used the same way in reports/services/sales_reports.py.
        """
        order, item = self._order(grand_total=105, item_status="ready")
        with patch.dict(sys.modules, {"pytz": None}):
            resp = self._login(self.manager).post(reverse("log-bypass", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.status, "served")

    def test_token_order_ready_item_is_not_touched_by_dine_in_close(self):
        from datetime import date
        from tokens.models import TokenOrder
        from orders.services.payment_service import process_payment
        order, item = self._order(grand_total=105, item_status="ready")
        order.source = "counter"
        order.save(update_fields=["source"])
        TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            token_number=1, date=date.today(), is_online=False,
        )
        process_payment(order, "cash", 105, user=self.owner)
        item.refresh_from_db()
        self.assertEqual(item.status, "ready", "token orders keep their own -- unrelated -- serve path")


class CreateKotDoesNotClobberBillingTableStateTest(TestCase):
    """
    create_kot() used to unconditionally write table.state = "preparing"
    whenever a KOT was sent, bypassing update_table_state()'s own guard
    against overwriting "billing"/"cleaning". Sending one more item to the
    kitchen for a table whose bill was already generated silently bounced
    it back to "preparing" -- the same class of bug as the order-splitting
    fix in orders/services/order_service.py::get_or_create_open_order,
    just via a second, independent raw write.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="KOT Table State Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.chef = User.objects.create_user(
            username="kot_chef", password="pw", tenant=self.tenant,
            outlet=self.outlet, role="chef",
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1", state="billing")
        self.category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category, name="Burger", price=100,
        )
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, status="billing",
        )
        self.order_item = OrderItem.objects.create(
            order=self.order, menu_item=self.item, quantity=1, price=100,
            gst_percentage=5, total_price=105, status="pending",
        )

    def test_sending_to_kitchen_leaves_a_billing_table_state_alone(self):
        create_kot(self.chef, self.order)
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "billing")

    def test_sending_to_kitchen_still_sets_preparing_for_a_normal_table(self):
        """Regression guard -- the everyday case must still work."""
        self.table.state = "ordering"
        self.table.save(update_fields=["state"])
        create_kot(self.chef, self.order)
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "preparing")


# ======================================================================
#  Moved from orders/tests/test_schema_review.py
# ======================================================================

def _index_names(model):
    return [idx.name for idx in model._meta.indexes]


def _field_names_in_indexes(model):
    return [tuple(idx.fields) for idx in model._meta.indexes]


class TestKOTBatchIndexes(TestCase):

    def test_tenant_outlet_status_composite_present(self):
        from kitchen.models import KOTBatch
        self.assertIn("kotbatch_tenant_outlet_status", _index_names(KOTBatch))

    def test_tenant_outlet_base_index_present(self):
        from kitchen.models import KOTBatch
        field_sets = _field_names_in_indexes(KOTBatch)
        self.assertIn(("tenant", "outlet"), field_sets)


# ======================================================================
#  Moved from orders/tests/test_critical.py
# ======================================================================

class KOTCounterTenantIsolationTest(TestCase):

    def setUp(self):
        self.t1 = Tenant.objects.create(name="KOT Cafe A")
        self.o1 = Outlet.objects.create(tenant=self.t1, name="Main")
        self.t2 = Tenant.objects.create(name="KOT Cafe B")
        self.o2 = Outlet.objects.create(tenant=self.t2, name="Main")

    def test_counter_per_tenant_outlet(self):
        from django.utils import timezone
        from kitchen.models import DailyKOTCounter
        today = timezone.now().date()

        c1, _ = DailyKOTCounter.objects.get_or_create(
            tenant=self.t1, outlet=self.o1, date=today
        )
        c2, _ = DailyKOTCounter.objects.get_or_create(
            tenant=self.t2, outlet=self.o2, date=today
        )
        c1.value = 5
        c1.save()
        c2.refresh_from_db()
        # Cafe B's counter must NOT be affected by Cafe A's increment
        self.assertEqual(c2.value, 0, "Counters must be isolated per tenant")

    def test_same_tenant_same_outlet_unique_per_day(self):
        from django.utils import timezone
        from kitchen.models import DailyKOTCounter
        today = timezone.now().date()
        DailyKOTCounter.objects.get_or_create(
            tenant=self.t1, outlet=self.o1, date=today
        )
        with self.assertRaises(Exception):
            # duplicate should fail
            DailyKOTCounter.objects.create(
                tenant=self.t1, outlet=self.o1, date=today, value=99
            )
