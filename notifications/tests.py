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