# orders/services/void_service.py
from django.utils import timezone
from django.db import transaction

from orders.models import OrderItem
from orders.services.event_service import log_event


@transaction.atomic
def void_order_item(user, item_id, reason):

    item = OrderItem.objects.select_related("order").get(id=item_id)

    if item.status in ["served", "voided"]:
        raise Exception("Item cannot be voided")

    item.status = "voided"
    item.void_reason = reason
    item.voided_by = user
    item.voided_at = timezone.now()

    item.save(update_fields=[
        "status",
        "void_reason",
        "voided_by",
        "voided_at"
    ])

    order = item.order

    order.recalculate_totals()

    log_event(
        order,
        "item_voided",
        user,
        {
            "item": item.menu_item.name,
            "reason": reason
        }
    )

    return item