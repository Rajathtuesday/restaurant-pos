# orders/services/void_service.py
import logging
from decimal import Decimal

from django.db.models import F
from django.utils import timezone
from django.db import transaction

from orders.models import Order, OrderItem
from orders.exceptions import OrderError
from orders.services.event_service import log_event

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
    if pre_void_status not in ("pending",) and item.menu_item:
        _restore_inventory_for_void(item)

    # Lock the Order row before recalculating totals.
    order = Order.objects.select_for_update().get(id=item.order_id)
    order.recalculate_totals()

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
    Uses the same recipe linkage that deduct_inventory_for_items uses.
    """
    from inventory.models import InventoryItem, InventoryTransaction

    recipes = list(
        getattr(item.menu_item, "recipes", item.menu_item.recipes if item.menu_item else None).all()
        if item.menu_item else []
    )
    if not recipes:
        return

    # Sort by inventory_item_id for consistent lock ordering (deadlock prevention)
    recipes = sorted(recipes, key=lambda r: r.inventory_item_id)
    inv_ids = [r.inventory_item_id for r in recipes]

    locked = {
        inv.id: inv
        for inv in InventoryItem.objects.select_for_update().filter(id__in=inv_ids)
    }

    txns = []
    for recipe in recipes:
        inv = locked.get(recipe.inventory_item_id)
        if not inv:
            logger.error(
                "Void restore: inventory item %s not found for recipe on %s",
                recipe.inventory_item_id, item.menu_item.name,
            )
            continue

        qty_to_restore = Decimal(str(recipe.quantity_required)) * Decimal(str(item.quantity))
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