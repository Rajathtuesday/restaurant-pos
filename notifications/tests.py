# notifications/tests.py
from unittest.mock import MagicMock, patch

from django.test import TestCase
from tenants.models import Tenant, Outlet
from notifications.models import Notification
from notifications.services.notification_service import create_notification
from notifications.services.whatsapp_service import _build_message


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


class WhatsAppBuildMessageTest(TestCase):
    """_build_message() must never raise — a broken item list should still
    produce a sendable receipt, but the failure must be logged, not silently
    swallowed."""

    def _mock_order(self):
        order = MagicMock()
        order.tenant.name = "Test Cafe"
        order.order_number = "INV-1"
        order.id = 42
        order.subtotal = 100
        order.tax_amount = 5
        order.grand_total = 105
        return order

    def test_normal_message_includes_items(self):
        order = self._mock_order()
        item = MagicMock()
        item.quantity = 2
        item.menu_item.name = "Butter Naan"
        item.total_price = 60
        order.items.select_related.return_value.all.return_value = [item]

        msg = _build_message(order, "")
        self.assertIn("Butter Naan", msg)
        self.assertIn("Total", msg)

    @patch("notifications.services.whatsapp_service.logger")
    def test_broken_item_list_logs_and_still_builds_message(self, mock_logger):
        order = self._mock_order()
        order.items.select_related.return_value.all.side_effect = Exception("db hiccup")

        msg = _build_message(order, "")  # must not raise

        self.assertIn("Total", msg)
        self.assertNotIn("x  Butter Naan", msg)

        mock_logger.warning.assert_called_once()
        args = mock_logger.warning.call_args[0]
        self.assertIn("order %s", args[0])
        self.assertEqual(args[1], 42)