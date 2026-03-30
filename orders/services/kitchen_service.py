# orders/services/kitchen_service.py
from django.db import transaction
from orders.models import OrderItem, KOTBatch
from notifications.services.notification_service import create_notification
from orders.services.order_service import update_table_state

def get_kitchen_data(user, station_name=None):
    """
    Retrieves and structures the active KOT batches for the kitchen display.
    """
    kots = KOTBatch.objects.filter(
        order__tenant=user.tenant,
        order__outlet=user.outlet,
        order__status="open"
    )

    if station_name:
        kots = kots.filter(station=station_name)

    kots = (
        kots
        .select_related("order", "order__table")
        .prefetch_related("items", "items__menu_item")
        .order_by("created_at")
    )

    data = []
    for kot in kots:
        items = []
        for i in kot.items.exclude(status__in=["served", "voided"]):
            items.append({
                "id": i.id,
                "name": i.menu_item.name,
                "quantity": i.quantity,
                "status": i.status,
                "notes": i.notes or ""
            })

        if not items:
            continue

        data.append({
            "id": kot.id,
            "kot_number": kot.kot_number,
            "station": kot.station,
            "table": kot.order.table.name if kot.order.table else "Takeaway",
            "created_at": kot.created_at.isoformat(),
            "items": items
        })

    return data


def set_item_preparing(user, item_id):
    """
    Marks a kitchen item as 'preparing'.
    """
    item = OrderItem.objects.get(
        id=item_id,
        order__tenant=user.tenant,
        order__outlet=user.outlet
    )

    if item.status != "sent":
        raise ValueError("Invalid state constraint: Item is not 'sent'.")

    item.status = "preparing"
    item.save(update_fields=["status"])
    return item


@transaction.atomic
def set_item_ready(user, item_id):
    """
    Marks an item as 'ready', updates table statuses, and pings notifications.
    """
    item = (
        OrderItem.objects
        .select_related("order")
        .select_for_update()
        .get(
            id=item_id,
            order__tenant=user.tenant,
            order__outlet=user.outlet
        )
    )

    if item.status != "preparing":
        raise ValueError("Invalid state constraint: Item is not 'preparing'.")

    item.status = "ready"
    item.save(update_fields=["status"])

    update_table_state(item.order)

    table_name = item.order.table.name if item.order.table else "Takeaway"
    create_notification(
        item.order.tenant,
        item.order.outlet,
        "order_ready",
        f"Order ready for {table_name}"
    )

    return item


def set_item_served(user, item_id):
    """
    Marks an item as 'served' and dynamically updates the table state.
    """
    item = OrderItem.objects.get(
        id=item_id,
        order__tenant=user.tenant,
        order__outlet=user.outlet
    )

    if item.status != "ready":
        raise ValueError("Item not ready yet!")

    item.status = "served"
    item.save(update_fields=["status"])

    update_table_state(item.order)
    return item
