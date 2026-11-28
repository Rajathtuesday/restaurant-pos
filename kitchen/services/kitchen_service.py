# kitchen/services/kitchen_service.py
from core.errors import UserError
from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef, Prefetch
from django.utils import timezone
from orders.models import OrderItem
from kitchen.models import KOTBatch
from orders.services.order_service import update_table_state
from orders.services.row_locks import lock_order_of_item

# Matches tokens/views.py's _READY_STALE_MINUTES (the pickup display
# board's own auto-clear) -- one "how long is a ready order allowed to
# sit before we assume it was collected" standard, used in both places.
_KDS_TOKEN_STALE_MINUTES = 5

# Item states that are finished as far as the kitchen display is concerned.
_DONE_ITEM_STATUSES = ["served", "voided"]



class KitchenStateError(UserError, ValueError):
    """A dish moved out of order on the kitchen screen (started twice,
    served before it was ready); the message says which."""


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

    The "still has unresolved items" rule is applied in the database, not
    in the loop. This used to load every KOT the outlet had ever had and
    then call kot.items.exclude(...) per KOT, which skips the prefetch:
    one query per KOT in history, plus one per item for its menu name. The
    screen polls every 5 s, and a load test measured about 1.1 ms per past
    KOT (1,000 KOTs = about 1 s per poll, 10,000 = about 12 s), so the
    kitchen display got slower every day a restaurant used it. Now it is
    two queries (KOTs with open items, then those items with their menu
    item) however long the history is.
    """
    unresolved = OrderItem.objects.exclude(status__in=_DONE_ITEM_STATUSES)

    kots = KOTBatch.objects.filter(
        order__tenant=user.tenant,
        order__outlet=user.outlet,
    ).exclude(order__status="cancelled").filter(
        Exists(unresolved.filter(kot=OuterRef("pk")))
    )

    if station_name:
        kots = kots.filter(station__name=station_name)

    kots = (
        kots
        .select_related("order", "order__table", "station", "order__token")
        .prefetch_related(Prefetch(
            "items",
            queryset=unresolved.select_related("menu_item").prefetch_related("modifiers").order_by("id"),
            to_attr="open_items",
        ))
        .order_by("created_at")
    )

    stale_cutoff = timezone.now() - timedelta(minutes=_KDS_TOKEN_STALE_MINUTES)

    data = []
    for kot in kots:
        token = getattr(kot.order, "token", None)
        if token and token.ready_at and token.ready_at < stale_cutoff:
            continue

        items = []
        for i in kot.open_items:
            items.append({
                "id": i.id,
                "name": i.menu_item.name,
                "quantity": i.quantity,
                "status": i.status,
                # What the cook makes it with (a guest's QR sauce choice has
                # no note at all), and what someone typed, each once.
                "modifiers": [m.name for m in i.modifiers.all()],
                "notes": i.typed_note,
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


# A dish's way through the kitchen, in order. "voided", and the two states
# before the kitchen ("review": a guest's QR dish waiting for staff;
# "pending": on the order, not sent yet), are off this path.
_KITCHEN_STEPS = ("sent", "preparing", "ready", "served")

# What a dish must be for each step to be taken now.
_COMES_FROM = {"preparing": "sent", "ready": "preparing", "served": "ready"}


def _should_move(item, step):
    """True when the dish moves to `step` now; False when it is already
    there or past it, which is answered as done; KitchenStateError, in
    kitchen words, when it cannot get there from where it is.

    Why (28 Nov 2026): a cook's phone on a slow link was a few seconds
    behind, and two more taps on Ready for a whisky that was already ready
    each got "Invalid state constraint: Item is not 'preparing'." The cook
    wanted the dish ready, and it was. A second screen, or a Bump crossing
    a Ready on the way, is the same. Nothing is written for a step already
    taken: no second "is ready" message, no second change to the table."""
    if item.status == _COMES_FROM[step]:
        return True
    if item.status in _KITCHEN_STEPS and _KITCHEN_STEPS.index(item.status) >= _KITCHEN_STEPS.index(step):
        return False
    raise KitchenStateError(_why_not(item, step))


def _why_not(item, step):
    name = item.menu_item.name if item.menu_item_id else "This dish"
    if item.status == "voided":
        return f"{name} was cancelled: there is nothing to do for it."
    if item.status in ("review", "pending"):
        return f"{name} has not been sent to the kitchen yet."
    if step == "ready":                         # still "sent"
        return f"Tap Start Prep on {name} first."
    return f"{name} is not ready yet."          # serving one still being made


@transaction.atomic
def set_item_preparing(user, item_id):
    """
    Marks a kitchen item as 'preparing'. True when it moved, False when it
    was already being made or past that (_should_move).
    """
    lock_order_of_item(user, item_id)  # order before line, see row_locks
    item = (
        OrderItem.objects
        .select_related("menu_item")
        .select_for_update(of=("self",))
        .get(
            id=item_id,
            order__tenant=user.tenant,
            order__outlet=user.outlet
        )
    )

    if not _should_move(item, "preparing"):
        return False

    item.status = "preparing"
    item.save(update_fields=["status"])
    return True


@transaction.atomic
def set_item_ready(user, item_id):
    """
    Marks an item as 'ready', updates table statuses, and pings notifications.
    True when it moved, False when it was already ready or served
    (_should_move).
    """
    lock_order_of_item(user, item_id)  # order before line, see row_locks
    item = (
        OrderItem.objects
        .select_related("order", "menu_item")
        .select_for_update(of=("self",))
        .get(
            id=item_id,
            order__tenant=user.tenant,
            order__outlet=user.outlet
        )
    )

    if not _should_move(item, "ready"):
        return False

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

    return True


@transaction.atomic
def set_item_served(user, item_id):
    """
    Marks an item as 'served' and dynamically updates the table state.
    True when it moved, False when it was already served (_should_move).
    """
    lock_order_of_item(user, item_id)  # order before line, see row_locks
    item = (
        OrderItem.objects
        .select_related("order", "menu_item")
        .select_for_update(of=("self",))
        .get(
            id=item_id,
            order__tenant=user.tenant,
            order__outlet=user.outlet
        )
    )

    if not _should_move(item, "served"):
        return False

    item.status = "served"
    item.save(update_fields=["status"])

    update_table_state(item.order)
    return True


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
