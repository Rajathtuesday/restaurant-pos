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
