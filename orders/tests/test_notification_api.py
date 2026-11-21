# orders/tests/test_notification_api.py
"""
Coverage for orders.api.notification_api's "notifications" key -- the
poller in templates/core/base.html was fetching this every 8 seconds and
silently discarding it, so low_stock/system Notification rows never
reached any user despite the backend already returning them correctly.
Confirms the backend contract these tests rely on stays correct now that
the frontend actually consumes it.

Run: python manage.py test orders.tests.test_notification_api
"""
from django.test import TestCase, Client
from tenants.models import Tenant, Outlet
from accounts.models import User
from notifications.models import Notification


class NotificationApiSystemAlertsTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Alerts Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(
            username="alerts_mgr", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.client = Client()
        self.client.login(username="alerts_mgr", password="pw")

    def test_unread_low_stock_notifications_are_returned(self):
        Notification.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            type="low_stock", message="Flour low stock (2 kg)",
        )
        resp = self.client.get("/api/notifications/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("notifications", data)
        self.assertEqual(len(data["notifications"]), 1)
        self.assertEqual(data["notifications"][0]["message"], "Flour low stock (2 kg)")

    def test_only_low_stock_and_system_alerts_reach_the_badge(self):
        # 15 Nov 2026: "Order ready" messages were counted too, and a busy
        # demo showed 50 alerts over an inventory board with 2 low items.
        for i in range(3):
            Notification.objects.create(tenant=self.tenant, outlet=self.outlet,
                                        type="order_ready", message=f"Order ready for Table {i}")
        Notification.objects.create(tenant=self.tenant, outlet=self.outlet, type="system", message="Backup failed")
        Notification.objects.create(tenant=self.tenant, outlet=self.outlet, type="low_stock", message="Rice low")
        messages = {n["message"] for n in self.client.get("/api/notifications/").json()["notifications"]}
        self.assertEqual(messages, {"Backup failed", "Rice low"})

    def test_already_read_notifications_are_excluded(self):
        Notification.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            type="low_stock", message="Old alert", is_read=True,
        )
        resp = self.client.get("/api/notifications/")
        data = resp.json()
        self.assertEqual(len(data["notifications"]), 0)

    def test_other_outlets_notifications_not_leaked(self):
        other_outlet = Outlet.objects.create(tenant=self.tenant, name="Branch 2")
        Notification.objects.create(
            tenant=self.tenant, outlet=other_outlet,
            type="low_stock", message="Other branch's problem",
        )
        resp = self.client.get("/api/notifications/")
        data = resp.json()
        self.assertEqual(len(data["notifications"]), 0)

    def test_dead_unread_notifications_endpoint_is_gone(self):
        """
        notifications/views.py's unread_notifications view and its URL
        were removed entirely -- it was already unreachable (orders.urls
        registers the same literal path earlier in core/urls.py's include
        order) and unused by any frontend code. This just confirms the
        cleanup didn't leave a dangling, differently-broken route behind.
        """
        resp = self.client.get("/api/notifications/unread/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Still resolves to orders.api.notification_api (registered under
        # this same path too) -- shaped like the main endpoint, not a 404
        # and not the old view's response shape.
        self.assertIn("waiter_calls", data)


class QrOrdersBadgeTest(TestCase):
    """The QR Orders badge and button (21 Nov 2026). The badge counted any
    order with a guest's dish still waiting for approval, closed and
    cancelled bills too, so it read 1 over an empty floor; and on the floor
    page the button linked to the floor page itself."""

    def setUp(self):
        from core.tests.test_tenant_walk import Restaurant
        from tenants.models import TenantFeatureOverride
        self.r = Restaurant("Alpha", "qr-badge")
        for feature in ("qr_menu", "floor_plan"):
            TenantFeatureOverride.objects.update_or_create(tenant=self.r.tenant, feature=feature, defaults={"enabled": True})
        self.client = Client()
        self.client.force_login(self.r.owner)
        self.r.order.items.update(status="review")       # a guest's dish, waiting for staff

    def count(self):
        return self.client.get("/api/notifications/").json()["qr_orders"]["count"]

    def test_an_open_bill_with_a_dish_waiting_counts(self):
        self.r.order.status = "open"
        self.r.order.save(update_fields=["status"])
        self.assertEqual(self.count(), 1)

    def test_a_closed_or_cancelled_bill_does_not(self):
        for status in ("closed", "cancelled"):
            self.r.order.status = status
            self.r.order.save(update_fields=["status"])
            self.assertEqual(self.count(), 0, status)

    def test_the_button_is_on_other_screens_but_not_on_the_floor_it_links_to(self):
        self.assertIn('id="notif-badge-qr"', self.client.get("/billing/").content.decode())
        floor = self.client.get("/tables/").content.decode()
        self.assertNotIn('id="notif-badge-qr"', floor)
        self.assertNotIn('id="notif-badge-qr-mobile"', floor)
