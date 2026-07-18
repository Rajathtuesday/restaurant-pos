# orders/tests/test_captain_permissions.py
"""
Tests for the new "captain" staff role — a senior floor-service tier with
more access than waiter but less than manager, added per user request.

Confirmed scope (see the plan this was implemented from):
  - Discount/comp authority: same as cashier (uncapped apply_discount,
    apply_item_discount, make_item_complimentary).
  - Cancel/void: not-yet-served items only (matches cashier); voiding an
    already-served item still requires manager/owner.
  - Order history: all outlet orders, last 7 days (between waiter's
    own-orders-only-today and cashier's all-outlet-30-days).
  - Payment bypass (log_bypass): NOT granted, stays manager/owner only.
  - Admin/config surfaces (menu, staff, payments, promos, reports,
    inventory, shifts/cash sessions, refunds, table CRUD): NOT granted.

Run: python manage.py test orders.tests.test_captain_permissions
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from orders.services.void_service import void_order_item
from orders.exceptions import OrderError
from tenants.models import Tenant, Outlet


def _tenant_outlet(name="Captain Test Tenant"):
    tenant = Tenant.objects.create(name=name, tenant_type="fine_dining")
    outlet = Outlet.objects.create(tenant=tenant, name=f"{name} Main")
    return tenant, outlet


def _user(tenant, outlet, role, username):
    return User.objects.create_user(
        username=username, password="pw", tenant=tenant, outlet=outlet, role=role,
    )


class _Base(TestCase):
    def setUp(self):
        self.tenant, self.outlet = _tenant_outlet()
        self.captain = _user(self.tenant, self.outlet, "captain", "captain1")
        self.waiter = _user(self.tenant, self.outlet, "waiter", "waiter1")
        self.manager = _user(self.tenant, self.outlet, "manager", "manager1")
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Butter Naan", price=Decimal("60.00"),
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, status="open",
        )
        self.order_item = OrderItem.objects.create(
            order=self.order, menu_item=self.item, quantity=1, price=Decimal("60.00"),
            gst_percentage=5, total_price=Decimal("63.00"), status="pending",
        )

    def _login(self, user):
        client = Client()
        client.login(username=user.username, password="pw")
        return client


class CaptainGrantedPermissionsTest(_Base):
    """Actions captain should now be able to perform."""

    def test_captain_can_send_order_to_kitchen(self):
        resp = self._login(self.captain).post(reverse("send-to-kitchen", args=[self.order.id]))
        self.assertEqual(resp.status_code, 200)

    def test_captain_can_serve_a_ready_item(self):
        self.order_item.status = "ready"
        self.order_item.save(update_fields=["status"])
        resp = self._login(self.captain).post(reverse("serve-item", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)

    def test_captain_can_send_kitchen_message(self):
        resp = self._login(self.captain).post(
            reverse("send-kitchen-message", args=[self.order.id]),
            data=json.dumps({"message": "Table 1 is in a hurry"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_captain_can_apply_order_discount(self):
        resp = self._login(self.captain).post(
            reverse("apply-discount", args=[self.order.id]),
            data=json.dumps({"type": "percentage", "value": 10}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_captain_can_apply_item_discount(self):
        resp = self._login(self.captain).post(
            reverse("item-discount", args=[self.order_item.id]),
            data=json.dumps({"percent": 10}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_captain_can_make_item_complimentary(self):
        resp = self._login(self.captain).post(reverse("make-complimentary", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)
        self.order_item.refresh_from_db()
        self.assertTrue(self.order_item.is_complimentary)

    def test_captain_can_cancel_a_not_yet_served_item(self):
        resp = self._login(self.captain).post(reverse("cancel-item", args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 200)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "voided")

    def test_captain_can_create_a_token_order(self):
        franchise_tenant, franchise_outlet = _tenant_outlet("Captain Token Tenant")
        franchise_tenant.tenant_type = "franchise"
        franchise_tenant.save(update_fields=["tenant_type"])
        captain2 = _user(franchise_tenant, franchise_outlet, "captain", "captain_token")
        resp = self._login(captain2).post(
            reverse("create-token-order"), data=json.dumps({}), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_captain_can_view_crm_dashboard(self):
        resp = self._login(self.captain).get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_captain_can_create_reservation(self):
        future_time = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        resp = self._login(self.captain).post(
            reverse("create-reservation"),
            data=json.dumps({
                "name": "Walk-in Test", "phone": "9999999999",
                "guests": 2, "reservation_time": future_time,
            }),
            content_type="application/json",
        )
        self.assertIn(resp.status_code, [200, 201])


class CaptainDeniedPermissionsTest(_Base):
    """Admin/config surfaces and heavier overrides captain must NOT reach."""

    def test_captain_cannot_bypass_payment(self):
        resp = self._login(self.captain).post(reverse("log-bypass", args=[self.order.id]))
        self.assertEqual(resp.status_code, 403)

    def test_captain_cannot_create_menu_item(self):
        resp = self._login(self.captain).post(
            reverse("create_menu_item"),
            data=json.dumps({"name": "New Dish", "price": 100, "category": self.category.id}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_captain_cannot_reach_staff_creation_page(self):
        resp = self._login(self.captain).get(reverse("setup_staff"))
        # Not a JSON 403 - this view redirects non-owner/manager away.
        self.assertEqual(resp.status_code, 302)
        self.assertNotEqual(resp.url, reverse("setup_staff"))

    def test_captain_cannot_open_cash_session_list(self):
        resp = self._login(self.captain).get(reverse("cash-session-list"))
        self.assertEqual(resp.status_code, 403)

    def test_captain_cannot_approve_refund(self):
        resp = self._login(self.captain).post(reverse("approve-refund", args=[999]))
        self.assertEqual(resp.status_code, 403)

    def test_captain_cannot_manage_tables(self):
        resp = self._login(self.captain).post(
            reverse("manage-table"),
            data=json.dumps({"table_id": self.table.id, "action": "rename"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_captain_cannot_void_an_already_served_item(self):
        self.order_item.status = "served"
        self.order_item.save(update_fields=["status"])
        with self.assertRaises(OrderError):
            void_order_item(self.captain, self.order_item.id, "test void")
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "served")  # unchanged

    def test_manager_can_still_void_an_already_served_item(self):
        # Sanity check the block above is captain-specific, not broken generally.
        self.order_item.status = "served"
        self.order_item.save(update_fields=["status"])
        item = void_order_item(self.manager, self.order_item.id, "test void")
        self.assertEqual(item.status, "voided")


class CaptainOrderHistoryScopeTest(_Base):
    """Captain sees all outlet orders within a 7-day window - not just
    their own (unlike waiter), and not the full unrestricted history
    (unlike manager)."""

    def setUp(self):
        super().setUp()
        self.recent_order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table,
            status="paid", created_by=self.waiter,
        )
        Order.objects.filter(id=self.recent_order.id).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        self.old_order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table,
            status="paid", created_by=self.waiter,
        )
        Order.objects.filter(id=self.old_order.id).update(
            created_at=timezone.now() - timedelta(days=8)
        )

    def test_captain_sees_another_staff_members_recent_order(self):
        resp = self._login(self.captain).get(reverse("order-history"))
        self.assertEqual(resp.status_code, 200)
        order_ids = {o.id for o in resp.context["page"]}
        self.assertIn(self.recent_order.id, order_ids)

    def test_captain_does_not_see_an_8_day_old_order(self):
        resp = self._login(self.captain).get(reverse("order-history"))
        order_ids = {o.id for o in resp.context["page"]}
        self.assertNotIn(self.old_order.id, order_ids)

    def test_manager_still_sees_the_8_day_old_order(self):
        # Sanity check the 7-day window is captain-specific, not a global bug.
        resp = self._login(self.manager).get(reverse("order-history"))
        order_ids = {o.id for o in resp.context["page"]}
        self.assertIn(self.old_order.id, order_ids)
