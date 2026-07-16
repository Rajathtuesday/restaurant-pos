# orders/services/void_service.py
import logging
from decimal import Decimal

from django.db.models import F
from django.utils import timezone
from django.db import transaction

from orders.models import Order, OrderItem
from orders.exceptions import OrderError
from orders.services.event_service import log_event
from orders.services.order_service import update_table_state

logger = logging.getLogger("pos.orders")


@transaction.atomic
def void_order_item(user, item_id, reason):

    item = (
        OrderItem.objects
        .select_for_update()
        .select_related("order", "menu_item")
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

    # Capture status BEFORE voiding — determines whether inventory was deducted.
    # Inventory is deducted at KOT-send time (status → "sent").
    # "pending" items were never sent to kitchen, so no inventory was touched.
    pre_void_status = item.status

    item.status = "voided"
    item.void_reason = reason
    item.voided_by = user
    item.voided_at = timezone.now()

    item.save(update_fields=["status", "void_reason", "voided_by", "voided_at"])

    # ── Restore inventory if KOT was already sent ──────────────────────────
    # Only "sent" / "preparing" / "ready" / "served" statuses mean the KOT
    # was dispatched and inventory was deducted. "pending" = not yet sent.
    # "review" (QR guest item awaiting staff approval) has never even been
    # "pending" yet, so it's never had inventory deducted either — without
    # excluding it here too, voiding a review-status item would incorrectly
    # add phantom stock that was never actually taken.
    if pre_void_status not in ("pending", "review") and item.menu_item:
        _restore_inventory_for_void(item)

    # Lock the Order row before recalculating totals.
    order = Order.objects.select_for_update().get(id=item.order_id)
    order.recalculate_totals()
    update_table_state(order)

    log_event(
        order,
        "item_voided",
        user,
        {"item": item.menu_item.name if item.menu_item else "Unknown", "reason": reason},
    )

    return item


def _restore_inventory_for_void(item):
    """
    Reverse the inventory deduction made when the KOT was sent.
    Called only when the item had already been dispatched to the kitchen.
    Uses the same recipe/modifier linkage and unit conversion that
    deduct_inventory_for_items (orders/services/inventory_service.py) uses —
    this used to restore the raw recipe quantity with no conversion and no
    modifier handling at all, so a recipe in grams against a kg-tracked item
    over/under-restored stock by 1000x, and any modifier-driven deduction
    (e.g. "Extra Cheese") was never reversed on void at all.

    Note: deduct_inventory_for_items soft-drains to 0 rather than going
    negative when stock was already short, so a void can restore slightly
    more than was actually taken in that edge case — a pre-existing
    limitation of recomputing from the recipe formula rather than recording
    the exact deducted amount per KOT line; out of scope for this fix.
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

    restore_map = {}   # {inventory_item_id: (inv, qty_to_restore)}

    for recipe in recipes:
        qty = recipe_expected_quantity(
            recipe.quantity_required, recipe.unit, recipe.inventory_item,
            logger=logger, context=f"Void restore: Recipe {recipe.id} (menu item '{item.menu_item.name}')",
        )
        if qty is None:
            continue
        qty_to_restore = qty * Decimal(str(item.quantity))
        inv = recipe.inventory_item
        prev = restore_map.get(inv.id, (inv, Decimal("0")))
        restore_map[inv.id] = (inv, prev[1] + qty_to_restore)

    for modifier, mod_recipe in modifier_links:
        qty = recipe_expected_quantity(
            mod_recipe.quantity_required, mod_recipe.unit, mod_recipe.inventory_item,
            logger=logger, context=f"Void restore: ModifierRecipe {mod_recipe.id} (modifier '{modifier.name}')",
        )
        if qty is None:
            continue
        qty_to_restore = qty * Decimal(str(item.quantity))
        inv = mod_recipe.inventory_item
        prev = restore_map.get(inv.id, (inv, Decimal("0")))
        restore_map[inv.id] = (inv, prev[1] + qty_to_restore)

    if not restore_map:
        return

    # Lock in a consistent order (by ID) to prevent deadlocks, same as the
    # deduction path.
    inv_ids = sorted(restore_map.keys())
    locked = {
        inv.id: inv
        for inv in InventoryItem.objects.select_for_update().filter(id__in=inv_ids)
    }

    txns = []
    for inv_id in inv_ids:
        inv = locked.get(inv_id)
        if not inv:
            logger.error("Void restore: inventory item %s not found", inv_id)
            continue
        _, qty_to_restore = restore_map[inv_id]
        if qty_to_restore <= 0:
            continue
        InventoryItem.objects.filter(pk=inv.id).update(stock=F("stock") + qty_to_restore)
        txns.append(
            InventoryTransaction(
                item=inv,
                tenant=inv.tenant,
                outlet=inv.outlet,
                quantity=qty_to_restore,           # positive = returning stock
                transaction_type="adjustment",
                reference=f"Void of Order #{item.order_id} item {item.id}",
            )
        )
        logger.info(
            "Inventory restored: +%s %s of %s (void of order #%s item #%s)",
            qty_to_restore, inv.unit, inv.name, item.order_id, item.id,
        )

    if txns:
        InventoryTransaction.objects.bulk_create(txns)