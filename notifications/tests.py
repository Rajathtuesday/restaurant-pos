# notifications/tests.py
from django.test import TestCase
from tenants.models import Tenant, Outlet
from notifications.models import Notification
from notifications.services.notification_service import create_notification


class NotificationModelTests(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Test Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Outlet"
        )

    def test_notification_creation(self):

        notification = Notification.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            type="system",
            message="Test notification"
        )

        self.assertEqual(notification.type, "system")

        self.assertFalse(notification.is_read)

    def test_notification_str(self):

        notification = Notification.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            type="system",
            message="Test message"
        )

        self.assertIn("Test message", str(notification))


class NotificationServiceTests(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Outlet"
        )

    def test_create_notification_service(self):

        notification = create_notification(
            tenant=self.tenant,
            outlet=self.outlet,
            type="low_stock",
            message="Cheese low stock"
        )

        self.assertEqual(notification.type, "low_stock")

        self.assertEqual(notification.message, "Cheese low stock")

        self.assertEqual(Notification.objects.count(), 1)


class UnreadNotificationsViewTest(TestCase):

    def setUp(self):
        from accounts.models import User
        self.tenant = Tenant.objects.create(name="View Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="View Outlet")
        self.user = User.objects.create_user(
            username="notif_user",
            password="pass123",
            role="cashier",
            tenant=self.tenant,
            outlet=self.outlet
        )

        # Create 3 unread notifications and 1 read
        for i in range(3):
            Notification.objects.create(
                tenant=self.tenant,
                outlet=self.outlet,
                type="system",
                message=f"Unread message {i}",
                is_read=False
            )
        Notification.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            type="system",
            message="Already read",
            is_read=True
        )

    def test_unread_endpoint_returns_only_unread(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/notifications/unread/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("notifications", data)
        self.assertEqual(len(data["notifications"]), 3)

    def test_fetching_marks_notifications_as_read(self):
        # Call the view function directly (bypasses the test-client/savepoint layer)
        # so we can verify the mark-as-read side-effect in the same DB connection.
        from django.test import RequestFactory
        from notifications.views import unread_notifications

        factory = RequestFactory()
        request = factory.get("/api/notifications/unread/")
        request.user = self.user

        response = unread_notifications(request)
        self.assertEqual(response.status_code, 200)

        still_unread = Notification.objects.filter(
            tenant=self.tenant,
            outlet=self.outlet,
            is_read=False
        ).count()
        self.assertEqual(still_unread, 0)

    def test_second_fetch_returns_empty(self):
        from django.test import RequestFactory
        from notifications.views import unread_notifications
        import json as _json

        factory = RequestFactory()
        request = factory.get("/api/notifications/unread/")
        request.user = self.user

        # First call — marks all as read
        unread_notifications(request)

        # Second call — should find nothing unread
        response = unread_notifications(request)
        data = _json.loads(response.content)
        self.assertEqual(len(data["notifications"]), 0)

    def test_unauthenticated_redirected(self):
        response = self.client.get("/api/notifications/unread/")
        self.assertIn(response.status_code, [301, 302])