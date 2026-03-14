# orders/services/event_service.py
from orders.models import OrderEvent


def log_event(order, event_type, user=None, metadata=None):

    OrderEvent.objects.create(
        tenant=order.tenant,
        outlet=order.outlet,
        order=order,
        event_type=event_type,
        created_by=user,
        metadata=metadata or {}
    )