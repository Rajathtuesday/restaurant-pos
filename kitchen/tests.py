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
