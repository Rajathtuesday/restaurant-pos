# notifications/services/notification_service.py
from notifications.models import Notification


def create_notification(tenant, outlet, type, message):

    Notification.objects.create(
        tenant=tenant,
        outlet=outlet,
        type=type,
        message=message
    )