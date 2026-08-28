# notifications/tests.py
from unittest.mock import MagicMock, patch

from django.test import TestCase
from tenants.models import Tenant, Outlet
from notifications.models import Notification
from notifications.services.notification_service import (
    create_notification, create_low_stock_alert, clear_low_stock_alert,
)
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


class LowStockAlertDedupTests(TestCase):
    """
    create_low_stock_alert/clear_low_stock_alert (notifications/services/
    notification_service.py) -- fixes every order that sold an already-low
    item creating a brand new Notification row instead of reusing the
    existing unread one, which let the header's Alerts badge balloon to
    dozens of rows for what was really one ongoing low-stock issue.
    """

    def setUp(self):
        from inventory.models import InventoryItem
        self.tenant = Tenant.objects.create(name="Dedup Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.item = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Paneer",
            unit="kg", stock=5, low_stock_threshold=10,
        )

    def test_second_alert_for_same_item_updates_instead_of_duplicating(self):
        create_low_stock_alert(self.tenant, self.outlet, self.item.id, "Paneer", "kg", 5)
        create_low_stock_alert(self.tenant, self.outlet, self.item.id, "Paneer", "kg", 3)

        self.assertEqual(Notification.objects.filter(type="low_stock").count(), 1)
        alert = Notification.objects.get(type="low_stock")
        self.assertIn("3 kg", alert.message)

    def test_alert_for_a_different_item_is_a_separate_row(self):
        from inventory.models import InventoryItem
        other = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Cheese",
            unit="kg", stock=1, low_stock_threshold=5,
        )
        create_low_stock_alert(self.tenant, self.outlet, self.item.id, "Paneer", "kg", 5)
        create_low_stock_alert(self.tenant, self.outlet, other.id, "Cheese", "kg", 1)

        self.assertEqual(Notification.objects.filter(type="low_stock").count(), 2)

    def test_new_alert_created_after_the_old_one_was_read(self):
        create_low_stock_alert(self.tenant, self.outlet, self.item.id, "Paneer", "kg", 5)
        Notification.objects.filter(type="low_stock").update(is_read=True)

        create_low_stock_alert(self.tenant, self.outlet, self.item.id, "Paneer", "kg", 2)

        self.assertEqual(Notification.objects.filter(type="low_stock").count(), 2)
        self.assertEqual(Notification.objects.filter(type="low_stock", is_read=False).count(), 1)

    def test_clear_marks_the_unread_alert_read(self):
        create_low_stock_alert(self.tenant, self.outlet, self.item.id, "Paneer", "kg", 5)
        clear_low_stock_alert(self.tenant, self.outlet, self.item.id)

        alert = Notification.objects.get(type="low_stock")
        self.assertTrue(alert.is_read)

    def test_clear_does_not_touch_a_different_items_alert(self):
        from inventory.models import InventoryItem
        other = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Cheese",
            unit="kg", stock=1, low_stock_threshold=5,
        )
        create_low_stock_alert(self.tenant, self.outlet, self.item.id, "Paneer", "kg", 5)
        create_low_stock_alert(self.tenant, self.outlet, other.id, "Cheese", "kg", 1)

        clear_low_stock_alert(self.tenant, self.outlet, self.item.id)

        self.assertTrue(Notification.objects.get(item_id=self.item.id, type="low_stock").is_read)
        self.assertFalse(Notification.objects.get(item_id=other.id, type="low_stock").is_read)


class ClearOrphanedLowStockAlertsMigrationTest(TestCase):
    """
    notifications/migrations/0004_clear_orphaned_low_stock_alerts.py --
    one-time cleanup for low_stock alerts created before the item FK
    existed (migration 0003). Those NULL-item rows can never be deduped
    against or auto-cleared by the fixed create_low_stock_alert/
    clear_low_stock_alert (they match on item_id), so without this they'd
    sit unread forever even after the dedup fix ships -- exactly why the
    header badge stayed inflated on a live tenant after the fix deployed.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Migration Cleanup Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

    def _run_migration_function(self):
        from importlib import import_module
        module = import_module("notifications.migrations.0004_clear_orphaned_low_stock_alerts")
        from django.apps import apps
        module.clear_orphaned_low_stock_alerts(apps, None)

    def test_orphaned_unread_low_stock_alert_gets_marked_read(self):
        orphan = Notification.objects.create(
            tenant=self.tenant, outlet=self.outlet, type="low_stock",
            message="Paneer low stock (2 kg)", item=None,
        )
        self._run_migration_function()
        orphan.refresh_from_db()
        self.assertTrue(orphan.is_read)

    def test_correctly_tagged_low_stock_alert_is_left_alone(self):
        from inventory.models import InventoryItem
        item = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Rice", unit="kg",
        )
        tagged = Notification.objects.create(
            tenant=self.tenant, outlet=self.outlet, type="low_stock",
            message="Rice low stock (1 kg)", item=item,
        )
        self._run_migration_function()
        tagged.refresh_from_db()
        self.assertFalse(tagged.is_read)

    def test_other_notification_types_are_left_alone(self):
        system_notif = Notification.objects.create(
            tenant=self.tenant, outlet=self.outlet, type="system",
            message="Heads up", item=None,
        )
        self._run_migration_function()
        system_notif.refresh_from_db()
        self.assertFalse(system_notif.is_read)

    def test_already_read_orphaned_alert_is_untouched(self):
        orphan = Notification.objects.create(
            tenant=self.tenant, outlet=self.outlet, type="low_stock",
            message="Paneer low stock (2 kg)", item=None, is_read=True,
        )
        original_created_at = orphan.created_at
        self._run_migration_function()
        orphan.refresh_from_db()
        self.assertTrue(orphan.is_read)
        self.assertEqual(orphan.created_at, original_created_at)


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