# kitchen/services/kitchen_service.py
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from orders.models import OrderItem
from kitchen.models import KOTBatch
from orders.services.order_service import update_table_state

# Matches tokens/views.py's _READY_STALE_MINUTES (the pickup display
# board's own auto-clear) -- one "how long is a ready order allowed to
# sit before we assume it was collected" standard, used in both places.
_KDS_TOKEN_STALE_MINUTES = 5


def get_kitchen_data(user, station_name=None):
    """
    Retrieves and structures the active KOT batches for the kitchen display.

    Deliberately does NOT restrict to order__status__in=["open", "billing"]
    -- that was correct for fine dining, where kitchen prep always happens
    before the bill is paid, but token (QSR/cafe) orders are the opposite:
    pay_order fires the KOT and immediately closes the order in the same
    request, so by the time anyone loads this screen the order is already
    "paid"/"closed" even though the kitchen hasn't prepared anything yet.
    Filtering on order status here excluded exactly the orders the kitchen
    most needed to see. What actually determines whether a KOT belongs on
    this screen is whether it still has unresolved items, which the
    per-item exclude(status__in=["served","voided"]) below already
    handles -- a KOT whose items are all done naturally produces an empty
    `items` list and gets skipped. Cancelled orders are excluded here as
    an explicit belt-and-braces measure (cancel_order already voids their
    non-served items, which the per-item filter would exclude anyway).

    Token orders get one extra rule dine-in orders deliberately don't:
    mark_token_collected ("Picked Up") already moves their items to
    "served", which is enough on its own when staff remember to tap it --
    but if they don't, a token order has no other path off this screen,
    since there's no waiter-serve step to fall back on. Once an order's
    been ready for more than _KDS_TOKEN_STALE_MINUTES, it's dropped here
    as a safety net, same cutoff the guest-facing pickup board already
    uses to assume "probably collected, stop cluttering the screen". This
    does NOT apply to dine-in/table orders -- a plated dish waiting under
    a heat lamp for a waiter isn't on a clock, and shouldn't vanish from
    the kitchen's own view of it just because time passed.
    """
    kots = KOTBatch.objects.filter(
        order__tenant=user.tenant,
        order__outlet=user.outlet,
    ).exclude(order__status="cancelled")

    if station_name:
        kots = kots.filter(station__name=station_name)

    kots = (
        kots
        .select_related("order", "order__table", "station", "order__token")
        .prefetch_related("items", "items__menu_item")
        .order_by("created_at")
    )

    stale_cutoff = timezone.now() - timedelta(minutes=_KDS_TOKEN_STALE_MINUTES)

    data = []
    for kot in kots:
        token = getattr(kot.order, "token", None)
        if token and token.ready_at and token.ready_at < stale_cutoff:
            continue

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
            "station": kot.station.name if kot.station else "General",
            "table": kot.order.table.name if kot.order.table else "Takeaway",
            "order_id": kot.order.id,
            "created_at": kot.created_at.isoformat(),
            "items": items
        })

    return data


@transaction.atomic
def set_item_preparing(user, item_id):
    """
    Marks a kitchen item as 'preparing'.
    """
    item = (
        OrderItem.objects
        .select_for_update()
        .get(
            id=item_id,
            order__tenant=user.tenant,
            order__outlet=user.outlet
        )
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

    # If this was the last item on the order still not ready/served/voided,
    # the whole order is now ready. Most QSR/cafe outlets run auto-KOT at
    # payment time with kitchen_display OFF, and staff set TokenOrder.ready_at
    # directly (see tokens/views.py::mark_token_ready) -- but an outlet that
    # DOES run a kitchen display needs this decided here instead, or the
    # order never reaches the pickup display board without a second, easily
    # forgotten manual tap on the Token Dashboard.
    _still_pending = item.order.items.filter(
        status__in=["review", "pending", "sent", "preparing"]
    ).exists()
    if not _still_pending:
        token = getattr(item.order, "token", None)
        if token is not None and token.ready_at is None:
            from django.utils import timezone as _tz
            token.ready_at = _tz.now()
            token.save(update_fields=["ready_at"])

    # Create KitchenMessage for the waiter dashboard -- this is the only
    # alert this event needs. It used to also write a "order_ready"
    # Notification row for the exact same event, but nothing ever surfaced
    # that channel to a user (the poller that fetches it never rendered
    # it), so it was a dead duplicate write, not a second real alert.
    from kitchen.models import KitchenMessage
    KitchenMessage.objects.create(
        tenant=item.order.tenant,
        outlet=item.order.outlet,
        order=item.order,
        message=f"{item.menu_item.name} is ready"
    )

    return item


@transaction.atomic
def set_item_served(user, item_id):
    """
    Marks an item as 'served' and dynamically updates the table state.
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

    if item.status != "ready":
        raise ValueError("Item not ready yet!")

    item.status = "served"
    item.save(update_fields=["status"])

    update_table_state(item.order)
    return item


@transaction.atomic
def clear_all_active_items(user):
    """
    Bulk-marks every currently unresolved item on this outlet's Kitchen
    Display "served" in one shot.

    Deliberately a blunt tool, not a proper per-order completion (it
    doesn't touch table state or TokenOrder.ready_at/collected_at the way
    the individual actions above do) -- it exists for clearing out
    stale/orphaned tickets (old test data, a KOT nobody ever finished
    tapping through) that have piled up on the screen, not for real
    service flow. Callers should require an explicit confirm before
    hitting this; the view itself restricts it to owner/manager.
    """
    items = (
        OrderItem.objects
        .select_for_update()
        .filter(
            order__tenant=user.tenant,
            order__outlet=user.outlet,
            status__in=["sent", "preparing", "ready"],
        )
        .exclude(order__status="cancelled")
    )
    count = items.count()
    items.update(status="served")
    return count
