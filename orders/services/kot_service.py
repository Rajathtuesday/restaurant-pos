# orders/services/kot_service.py

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from orders.models import KOTBatch, OrderItem, DailyKOTCounter
from orders.services.inventory_service import deduct_inventory

from django.db import transaction
from orders.models import Order, OrderItem


@transaction.atomic
def create_kot(user, order):

    # LOCK the order row to prevent duplicate KOTs
    order = (
        Order.objects
        .select_for_update()
        .get(id=order.id)
    )

    items = OrderItem.objects.filter(
        order=order,
        status="pending"
    ).select_related("menu_item")

    if not items.exists():
        raise Exception("No items to send to kitchen ")

    today = timezone.now().date()

    counter, _ = DailyKOTCounter.objects.select_for_update().get_or_create(
        date=today
    )

    counter.value = F("value") + 1
    counter.save(update_fields=["value"])
    counter.refresh_from_db()

    kot_number = counter.value

    kot = KOTBatch.objects.create(
        tenant=user.tenant,
        outlet=user.outlet,
        order=order,
        kot_number=kot_number,
        status="confirmed"
    )

    for item in items:

        deduct_inventory(item)

        item.kot = kot
        item.status = "sent"
        item.save(update_fields=["kot", "status"])

    if order.table:
        order.table.state = "preparing"
        order.table.save(update_fields=["state"])

    return kot
