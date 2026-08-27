# notifications/services/notification_service.py
import logging
from notifications.models import Notification

logger = logging.getLogger("pos.notifications")


def create_notification(tenant, outlet, type, message):

    notification = Notification.objects.create(
        tenant=tenant,
        outlet=outlet,
        type=type,
        message=message
    )

    logger.info(
        "[NOTIFICATION] tenant=%s outlet=%s type=%s message=%s",
        tenant.id, outlet.id, type, message,
    )

    return notification


def create_low_stock_alert(tenant, outlet, item_id, item_name, unit, new_stock):
    """
    One unread low-stock alert per item, not one per sale. If an unread
    low_stock notification already exists for this item, refreshes its
    message with the current stock level instead of creating a duplicate
    row -- previously every order that sold an already-low item created a
    brand new row, so one perpetually-low ingredient could produce dozens
    of alerts for the same ongoing issue.
    """
    message = f"{item_name} low stock ({new_stock} {unit})"
    updated = Notification.objects.filter(
        tenant=tenant, outlet=outlet, type="low_stock",
        item_id=item_id, is_read=False,
    ).update(message=message)
    if not updated:
        Notification.objects.create(
            tenant=tenant, outlet=outlet, type="low_stock",
            item_id=item_id, message=message,
        )
        logger.info(
            "[NOTIFICATION] tenant=%s outlet=%s type=low_stock item=%s message=%s",
            tenant.id, outlet.id, item_id, message,
        )


def clear_low_stock_alert(tenant, outlet, item_id):
    """
    Called once an item's stock rises back above its threshold (restock or
    PO receipt) -- an alert that's no longer true shouldn't need a manual
    trip to the inventory board to go away.
    """
    Notification.objects.filter(
        tenant=tenant, outlet=outlet, type="low_stock",
        item_id=item_id, is_read=False,
    ).update(is_read=True)