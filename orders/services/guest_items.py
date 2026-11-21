"""
A guest's QR dishes, waiting for a waiter's yes (status "review").

One rule for accepting them, whichever screen the waiter is on (the floor
sheet, the running order, the billing panel, the token counter): accept
one dish, some, or all of them in one step. Whether the kitchen gets a
ticket now depends on the order, not on the button: a dine-in order eats
first and pays after, so its accepted dishes go to the kitchen at once; a
pay-first order (a token) gets its ticket at payment, as pay_order does.

Before 21 Nov 2026 there were two rules: "approve all" sent a ticket and
"approve one" never did, so a dine-in dish approved on its own was accepted
but never reached the kitchen.

Not accepting a dish is refuse_guest_item: the dish is cancelled with a
reason, and the guest's phone says so (OrderItem.not_accepted).
"""
import logging

from django.db import transaction

from orders.exceptions import OrderError
from orders.models import Order, OrderItem
from orders.services.event_service import log_event
from orders.services.void_service import _lock_line, void_order_item

logger = logging.getLogger("pos.orders")

# The reasons a waiter taps when not accepting a guest's dish. Only these
# are shown on the guest's phone; anything typed by hand is for the staff's
# void report and the guest sees no reason.
GUEST_REASONS = ("Not available", "Guest changed mind", "Ordered twice")


class NothingWaiting(OrderError):
    """None of the dishes asked for is still waiting for approval."""


def approve_guest_items(user, order_id, item_ids=None):
    """
    Accept a guest's dishes on one order: those in `item_ids`, or every one
    still waiting when it is None. Returns {"approved": [ids], "kots":
    [ticket numbers], "waiting": how many still wait}.

    Raises Order.DoesNotExist for an order outside the user's restaurant
    and outlet, and NothingWaiting when none of the dishes is waiting (a
    second tap, or another waiter got there first).
    """
    from kitchen.services.kot_service import NothingToSend, create_kot

    with transaction.atomic():
        # Order row first, then its lines (orders/services/row_locks.py).
        order = Order.objects.select_for_update().get(id=order_id, tenant=user.tenant, outlet=user.outlet)
        if order.status not in ("open", "billing"):
            raise OrderError(f"This order is {order.status}; nothing on it can be approved.")

        waiting = OrderItem.objects.select_for_update(of=("self",)).filter(order=order, status="review")
        if item_ids is not None:
            waiting = waiting.filter(id__in=item_ids)
        ids = sorted(waiting.values_list("id", flat=True))
        if not ids:
            raise NothingWaiting("Nothing here is waiting for approval any more.")

        OrderItem.objects.filter(id__in=ids).update(status="pending")

        kots = []
        if not order.pays_first:
            try:
                with transaction.atomic():
                    kots = create_kot(user, order)
            except NothingToSend:
                pass
            except Exception:
                # The dishes stay accepted and unsent; Send to Kitchen sends
                # them. Better than refusing the approval over a printer.
                logger.exception("Kitchen ticket failed after approving guest dishes on order #%s", order.id)

        log_event(order, "status_changed", user, {
            "action": "items_approved", "count": len(ids), "item_ids": ids,
            "kots": [k.kot_number for k in kots],
        })
        still = OrderItem.objects.filter(order=order, status="review").count()

    logger.info("User %s approved %s guest dish(es) on order #%s", user.username, len(ids), order_id)
    return {"approved": ids, "kots": [k.kot_number for k in kots], "waiting": still}


def refuse_guest_item(user, item_id, reason, approver=None):
    """
    Not accept one guest dish that is still waiting: it is cancelled with
    `reason` and marked not_accepted, so the guest's phone says so. The
    kitchen never saw it, so no manager PIN is needed, unless the bill is
    already printed (void_order_item's usual rule, read from `approver`).

    Raises OrderItem.DoesNotExist outside the user's restaurant and outlet,
    and NothingWaiting when the dish was already accepted or cancelled.
    """
    with transaction.atomic():
        item = _lock_line(user, item_id)
        if item.status != "review":
            raise NothingWaiting("This dish is no longer waiting for approval. Use Remove to take it off.")
        return void_order_item(user, item.id, reason or "Not accepted", approver=approver)


def guest_reason(item):
    """The reason a guest may see for a dish not accepted: one of the
    GUEST_REASONS chips, else none (a hand-typed reason stays with staff)."""
    return item.void_reason if item.void_reason in GUEST_REASONS else ""
