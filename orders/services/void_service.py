# orders/services/void_service.py
import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import F
from django.utils import timezone
from django.db import transaction

from orders.models import Order, OrderItem, OrderEvent
from orders.exceptions import OrderError
from orders.services.event_service import log_event
from orders.services.order_service import update_table_state
from orders.services.row_locks import lock_order_of_item

logger = logging.getLogger("pos.orders")

_NOT_YET_IN_KITCHEN = ("pending", "review")
_ALREADY_MADE = ("ready", "served")
_MANAGERS = ("manager", "owner")
_TWO_PLACES = Decimal("0.01")

# A ticket that has sat with the kitchen this long is treated as cooked unless
# someone says otherwise. Many kitchens never tap "Start prep", so the status
# alone would keep saying "sent" long after the food is made.
MADE_AFTER = timedelta(minutes=10)


class ManagerRequired(OrderError):
    """The action is allowed, but only for a manager or owner."""


class MadeDishesNeedConfirmation(OrderError):
    """Cancelling this order would throw away dishes the kitchen already made."""

    def __init__(self, items):
        self.items = items
        n = sum(i.quantity for i in items)
        super().__init__(
            f"{n} dish{'es' if n != 1 else ''} on this order {'were' if n != 1 else 'was'} "
            f"already made. Cancel them as wastage or keep them and go to the bill."
        )


# ── was the dish actually made? ─────────────────────────────────────────

def suggest_made(status, kot_sent_at, now=None):
    """Best guess at whether the kitchen has already made this dish."""
    if status in _NOT_YET_IN_KITCHEN:
        return False
    if status in _ALREADY_MADE or status == "preparing":
        return True
    # "sent": made if the ticket has been in the kitchen long enough.
    if kot_sent_at is None:
        return False
    return ((now or timezone.now()) - kot_sent_at) >= MADE_AFTER


def kitchen_stock_hint(item, is_manager, now=None):
    """
    What the edit sheet should offer for one line: whether it's with the
    kitchen, whether "made" is fixed, the pre-selected answer, and whether
    switching a likely-made dish back to stock needs a manager. Reads
    item.kot, so callers should select_related("kot").
    """
    in_kitchen = item.status not in _NOT_YET_IN_KITCHEN
    sent_at = item.kot.created_at if item.kot_id else None
    made = suggest_made(item.status, sent_at, now)
    locked = item.status in _ALREADY_MADE
    return {
        "in_kitchen": in_kitchen,
        "made_locked": in_kitchen and locked,
        "suggest_made": in_kitchen and made,
        "restock_needs_manager": in_kitchen and made and not locked and not is_manager,
    }


def _kot_sent_at(item):
    if not item.kot_id:
        return None
    from kitchen.models import KOTBatch
    return KOTBatch.objects.filter(id=item.kot_id).values_list("created_at", flat=True).first()


def _decide_made(item, user, made):
    """
    Returns (in_kitchen, made). `made` is True/False from the person
    cancelling, or None to use the suggestion. Ready and served dishes were
    made, full stop. Anyone may call a dish wasted; putting a dish the
    kitchen has probably started back into stock needs a manager, or losses
    could be hidden.
    """
    if item.status in _NOT_YET_IN_KITCHEN:
        return False, False
    suggested = suggest_made(item.status, _kot_sent_at(item))
    if made is None:
        return True, suggested
    if made is False and item.status in _ALREADY_MADE:
        raise OrderError("Ready or served dishes were already made, so their ingredients count as wastage.")
    if made is False and suggested and getattr(user, "role", None) not in _MANAGERS:
        raise ManagerRequired("A manager is needed to put a dish the kitchen has started back into stock.")
    return True, bool(made)


# ── cancelling one line ─────────────────────────────────────────────────

def _lock_line(user, item_id):
    """
    Lock one order line for a change: its order first, then the line (see
    row_locks.lock_order_of_item). of=("self",) keeps the menu item row out
    of the lock; the old joined FOR UPDATE locked that too.
    """
    order_id = lock_order_of_item(user, item_id)
    return (
        OrderItem.objects
        .select_for_update(of=("self",))
        .select_related("order", "menu_item")
        .get(id=item_id, order_id=order_id)
    )


@transaction.atomic
def void_order_item(user, item_id, reason, made=None):

    item = _lock_line(user, item_id)

    if item.order.status in ("paid", "closed", "cancelled"):
        raise OrderError("Cannot void an item on a completed order.")
    if item.status == "voided":
        raise OrderError("Item is already voided")
    if item.status == "served" and user.role not in _MANAGERS:
        raise ManagerRequired("Item is already served. Manager override required.")

    in_kitchen, made = _decide_made(item, user, made)

    item.status = "voided"
    item.void_reason = reason
    item.voided_by = user
    item.voided_at = timezone.now()

    item.save(update_fields=["status", "void_reason", "voided_by", "voided_at"])

    # Stock was taken when the ticket went to the kitchen. Nothing to undo for
    # a dish that never got there; otherwise it goes back or becomes wastage.
    stock = "none"
    if in_kitchen and item.menu_item:
        stock = "wasted" if made else "returned"
        _move_stock_for_cancelled(item, made, reason)

    # Lock the Order row before recalculating totals.
    order = Order.objects.select_for_update().get(id=item.order_id)
    order.recalculate_totals()
    update_table_state(order)

    log_event(
        order,
        "item_voided",
        user,
        {"item": item.menu_item.name if item.menu_item else "Unknown", "reason": reason,
         "quantity": item.quantity, "stock": stock},
    )

    return item


def _whole_units(value):
    # Accepts 2, 2.0 and "2"; refuses 1.5, "1.5", True and junk instead of
    # quietly rounding them to some other number of dishes.
    if isinstance(value, bool):
        raise OrderError("Quantity to remove must be a whole number.")
    if isinstance(value, float):
        if not value.is_integer():
            raise OrderError("Quantity to remove must be a whole number.")
        value = int(value)
    try:
        units = int(str(value).strip())
    except (TypeError, ValueError):
        raise OrderError("Quantity to remove must be a whole number.")
    if units < 1:
        raise OrderError("Quantity to remove must be at least 1.")
    return units


@transaction.atomic
def reduce_item_quantity(user, item_id, reduce_by, reason, made=None):
    """
    Take `reduce_by` units off one order line ("make that one naan, not two").

    Before the line has gone to the kitchen it's just a basket edit: the
    quantity drops in place, no void record, no stock movement.

    Once it has gone to the kitchen, the removed units are split off into
    their own voided line (same dish, price, GST, discount, modifiers, KOT)
    and the original line keeps the rest. That way the void report shows
    exactly what was taken back and why, and every sales report (which
    already skips voided lines) stays right. The removed units' ingredients
    go back to stock or become wastage by the same rule as a full cancel.
    No schema change needed.

    Removing the whole quantity is the same as voiding the line.
    """
    reduce_by = _whole_units(reduce_by)

    item = _lock_line(user, item_id)

    if item.order.status in ("paid", "closed", "cancelled"):
        raise OrderError("Cannot change an item on a completed order.")
    if item.status == "voided":
        raise OrderError("Item is already voided")
    if item.status == "served" and user.role not in _MANAGERS:
        raise ManagerRequired("Item is already served. Manager override required.")
    if reduce_by > item.quantity:
        raise OrderError(f"Only {item.quantity} left on this line.")

    if reduce_by == item.quantity:
        return void_order_item(user, item.id, reason, made=made)

    # total_price is (price + modifier prices) x quantity, so it divides evenly.
    unit_total = item.total_price / Decimal(item.quantity)
    removed_total = (unit_total * reduce_by).quantize(_TWO_PLACES)
    name = item.menu_item.name if item.menu_item else "Unknown"
    before = item.quantity

    if item.status in _NOT_YET_IN_KITCHEN:
        item.quantity -= reduce_by
        item.total_price -= removed_total
        item.save(update_fields=["quantity", "total_price"])
        order = Order.objects.select_for_update().get(id=item.order_id)
        order.recalculate_totals()
        log_event(order, "item_updated", user, {
            "action": "quantity_reduced", "item": name,
            "from": before, "to": item.quantity, "reason": reason,
        })
        return item

    _, made = _decide_made(item, user, made)

    from orders.models import OrderItemModifier

    voided = OrderItem.objects.create(
        order_id=item.order_id,
        menu_item=item.menu_item,
        quantity=reduce_by,
        price=item.price,
        item_discount_pct=item.item_discount_pct,
        gst_percentage=item.gst_percentage,
        total_price=removed_total,
        status="voided",
        is_takeaway=item.is_takeaway,
        is_complimentary=item.is_complimentary,
        notes=item.notes,
        void_reason=reason,
        voided_by=user,
        voided_at=timezone.now(),
        kot_id=item.kot_id,
    )
    OrderItemModifier.objects.bulk_create([
        OrderItemModifier(order_item=voided, modifier_id=m.modifier_id, name=m.name, price=m.price)
        for m in item.modifiers.all()
    ])

    item.quantity -= reduce_by
    # Subtract rather than recompute, so the two lines always add back up to
    # exactly what the original line was.
    item.total_price -= removed_total
    item.save(update_fields=["quantity", "total_price"])

    if item.menu_item:
        _move_stock_for_cancelled(voided, made, reason)

    order = Order.objects.select_for_update().get(id=item.order_id)
    order.recalculate_totals()
    update_table_state(order)

    # "item_voided" on purpose: the Discount & Void audit counts this event
    # type, so a partial reduction shows up there alongside full voids.
    log_event(order, "item_voided", user, {
        "item": name, "reason": reason, "quantity": reduce_by,
        "partial": True, "from": before, "to": item.quantity,
        "stock": "wasted" if made else "returned",
    })
    return item


# ── cancelling a whole order ────────────────────────────────────────────

@transaction.atomic
def cancel_whole_order(user, order_id, reason="", include_made=False):
    """
    Cancel every remaining dish on an order and close it as cancelled.

    Dishes the kitchen already made are never silently left behind (they used
    to stay "served" on a cancelled order, off every report). If there are
    any, the caller must confirm with include_made=True, give a reason, and,
    for served dishes, be a manager. Each dish's stock then follows the same
    rule as cancelling it on its own.
    """
    order = (
        Order.objects.select_for_update()
        .filter(id=order_id, tenant=user.tenant, outlet=user.outlet)
        .first()
    )
    if not order:
        raise Order.DoesNotExist
    if order.status in ("paid", "closed", "cancelled"):
        raise OrderError(f"Cannot cancel order in {order.status} state")

    active = list(order.items.exclude(status="voided").select_related("menu_item", "kot"))
    now = timezone.now()
    made = [
        i for i in active
        if suggest_made(i.status, i.kot.created_at if i.kot_id else None, now)
    ]

    if made and not include_made:
        raise MadeDishesNeedConfirmation(made)
    if any(i.status == "served" for i in active) and user.role not in _MANAGERS:
        raise ManagerRequired("A manager is needed to cancel dishes that were already served.")
    reason = (reason or "").strip()[:255]
    if made and not reason:
        raise OrderError("Pick a reason. Cancelled dishes that were already made go on the void report.")
    reason = reason or "Order cancelled"

    for item in active:
        try:
            void_order_item(user, item.id, reason)
        except OrderError:
            # Voided by a concurrent action since the list above was built.
            continue

    order.refresh_from_db()
    order.status = "cancelled"
    order.closed_at = timezone.now()
    order.save(update_fields=["status", "closed_at"])
    order.recalculate_totals()

    # Cancelled orders are excluded from every active-order query, so free the
    # table unconditionally.
    if order.table:
        order.table.state = "free"
        order.table.save(update_fields=["state"])

    OrderEvent.objects.create(
        tenant=order.tenant, outlet=order.outlet, order=order,
        event_type="order_cancelled",
        metadata={"reason": reason, "dishes": len(active), "already_made": len(made)},
        created_by=user,
    )
    return order


# ── stock ───────────────────────────────────────────────────────────────

def _move_stock_for_cancelled(item, made, reason=""):
    """
    Undo, or re-label, the stock taken when this line's ticket went to the
    kitchen. Uses the same recipe/modifier linkage and unit conversion as
    deduct_inventory_for_items (orders/services/inventory_service.py).

    Not made: the ingredients go back on the shelf. Logged as a positive
    "consume" row, i.e. a reversal of the original use, so usage, food cost
    and the variance report all net to zero for this dish.

    Made: the ingredients are gone, so stock doesn't move. The use is
    re-labelled instead: a positive "consume" row cancels "used for a sale"
    and a matching "wastage" row records the loss. Both rows point at the
    cancelled line, which is what the wastage report filters on.

    Note: deduct_inventory_for_items soft-drains to 0 rather than going
    negative when stock was already short, so a return can put back slightly
    more than was actually taken in that edge case. A pre-existing limitation
    of recomputing from the recipe rather than recording the exact deducted
    amount per ticket line.
    """
    from inventory.models import InventoryItem, InventoryTransaction, ModifierRecipe
    from inventory.unit_conversion import recipe_expected_quantity
    from orders.models import OrderItemModifier

    if not item.menu_item:
        return

    recipes = list(item.menu_item.recipes.select_related("inventory_item").all())
    modifier_links = [
        (oim.modifier, mr)
        for oim in OrderItemModifier.objects.filter(order_item=item).select_related("modifier")
        if oim.modifier
        for mr in ModifierRecipe.objects.filter(modifier=oim.modifier).select_related("inventory_item")
    ]
    if not recipes and not modifier_links:
        return

    qty_map = {}   # {inventory_item_id: qty}

    for recipe in recipes:
        qty = recipe_expected_quantity(
            recipe.quantity_required, recipe.unit, recipe.inventory_item,
            logger=logger, context=f"Cancel stock: Recipe {recipe.id} (menu item '{item.menu_item.name}')",
        )
        if qty is None:
            continue
        inv_id = recipe.inventory_item_id
        qty_map[inv_id] = qty_map.get(inv_id, Decimal("0")) + qty * Decimal(str(item.quantity))

    for modifier, mod_recipe in modifier_links:
        qty = recipe_expected_quantity(
            mod_recipe.quantity_required, mod_recipe.unit, mod_recipe.inventory_item,
            logger=logger, context=f"Cancel stock: ModifierRecipe {mod_recipe.id} (modifier '{modifier.name}')",
        )
        if qty is None:
            continue
        inv_id = mod_recipe.inventory_item_id
        qty_map[inv_id] = qty_map.get(inv_id, Decimal("0")) + qty * Decimal(str(item.quantity))

    qty_map = {k: v for k, v in qty_map.items() if v > 0}
    if not qty_map:
        return

    dish = f"{item.quantity} x {item.menu_item.name}"
    where = f"order #{item.order_id}"
    txns = []

    if made:
        # No stock change and no row lock needed: only records are added.
        items = InventoryItem.objects.filter(id__in=qty_map.keys())
        for inv in items:
            qty = qty_map[inv.id]
            txns.append(InventoryTransaction(
                item=inv, tenant=inv.tenant, outlet=inv.outlet, order_item=item,
                quantity=qty, transaction_type="consume",
                reference=f"Moved to wastage: {dish} cancelled after cooking ({where})"[:255],
            ))
            txns.append(InventoryTransaction(
                item=inv, tenant=inv.tenant, outlet=inv.outlet, order_item=item,
                quantity=-qty, transaction_type="wastage",
                reference=f"{dish} cancelled after cooking ({where}): {reason}"[:255],
            ))
            logger.info("Wastage recorded: %s %s of %s (cancelled %s, %s)", qty, inv.unit, inv.name, dish, where)
    else:
        # Lock in a consistent order (by ID) to prevent deadlocks with the
        # deduction path and with other cancels.
        for inv in InventoryItem.objects.select_for_update().filter(id__in=qty_map.keys()).order_by("id"):
            qty = qty_map[inv.id]
            InventoryItem.objects.filter(pk=inv.id).update(stock=F("stock") + qty)
            txns.append(InventoryTransaction(
                item=inv, tenant=inv.tenant, outlet=inv.outlet, order_item=item,
                quantity=qty, transaction_type="consume",
                reference=f"Returned: {dish} cancelled before cooking ({where})"[:255],
            ))
            logger.info("Inventory restored: +%s %s of %s (cancelled %s, %s)", qty, inv.unit, inv.name, dish, where)

    if txns:
        InventoryTransaction.objects.bulk_create(txns)
