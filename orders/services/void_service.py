# orders/services/void_service.py
from django.utils import timezone
from django.db import transaction

from orders.models import Order, OrderItem
from orders.exceptions import OrderError
from orders.services.event_service import log_event


@transaction.atomic
def void_order_item(user, item_id, reason):

    item = (
        OrderItem.objects
        .select_for_update()
        .select_related("order")
        .get(
            id=item_id,
            order__tenant=user.tenant,
            order__outlet=user.outlet
        )
    )

    if item.status == "voided":
        raise OrderError("Item is already voided")
    if item.status == "served" and user.role not in ["manager", "owner"]:
        raise OrderError("Item is already served. Manager override required.")

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

    # Lock the Order row before recalculating so concurrent voids on the same
    # order cannot read a stale item set and overwrite each other's totals.
    order = Order.objects.select_for_update().get(id=item.order_id)

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