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