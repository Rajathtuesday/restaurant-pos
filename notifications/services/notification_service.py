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
        f"[NOTIFICATION] tenant={tenant.id} "
        f"outlet={outlet.id} "
        f"type={type} "
        f"message={message}"
    )

    return notification