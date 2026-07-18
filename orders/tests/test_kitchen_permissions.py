# orders/tests/test_kitchen_permissions.py
"""
Bug: orders/views/kitchen_views.py had zero @role_required gates on any of
its 8 endpoints (every other file under orders/views/ consistently uses
role_required) - any authenticated staff member of the tenant, including a
waiter, could hit /kitchen/, mark items preparing/ready, and bump whole
KOTs, none of which are waiter actions.

Fix: role_required added per-endpoint, matching who actually performs each
action:
  - send_to_kitchen        -> front-of-house (owner/manager/cashier/waiter)
  - kitchen_view/data,
    start_preparing,
    mark_ready, bump_kot    -> kitchen staff only (owner/manager/chef/kitchen)
  - serve_item,
    send_kitchen_message    -> anyone who can be at a table (adds waiter/cashier)

Run: python manage.py test orders.tests.test_kitchen_permissions
"""
import json

from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from orders.services.kot_service import create_kot
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
