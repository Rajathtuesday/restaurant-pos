
# orders/services/inventory_service.py

from django.db import transaction
import logging

logger = logging.getLogger("pos.inventory")
from django.core.exceptions import ObjectDoesNotExist

from inventory.models import InventoryItem


def deduct_inventory_for_items(order_items):
    """
    Deduct inventory for a list of OrderItems in bulk.
    Locks inventory items in ID order to prevent deadlocks.
    Uses soft-drain to 0 if stock is short (to prevent KOT failure),
    but logs aggressively. Avoids nested transactions.
    """
    from collections import defaultdict
    from decimal import Decimal
    from django.db.models import F
    from inventory.models import InventoryTransaction

    # 1. Aggregate total required quantities for each inventory item
    required_qty_map = defaultdict(Decimal)
    item_references = defaultdict(list)

    # 1a. Base recipe deductions (menu item → ingredient)
    for order_item in order_items:
        recipes_manager = getattr(order_item.menu_item, "recipes", None)
        if recipes_manager is None:
            continue
        for recipe in recipes_manager.all():
            req_qty = Decimal(str(recipe.quantity_required)) * Decimal(str(order_item.quantity))
            required_qty_map[recipe.inventory_item_id] += req_qty
            item_references[recipe.inventory_item_id].append(f"Order #{order_item.order_id}")

    # 1b. Modifier deductions (selected modifier → ingredient)
    from orders.models import OrderItemModifier
    from inventory.models import ModifierRecipe

    oi_qty = {oi.id: Decimal(str(oi.quantity)) for oi in order_items}
    all_oims = (
        OrderItemModifier.objects
        .filter(order_item_id__in=oi_qty)
        .select_related("modifier")
    )
    modifier_qty_map = defaultdict(list)   # {modifier_id: [(qty, label), ...]}
    for oim in all_oims:
        modifier_qty_map[oim.modifier_id].append(
            (oi_qty[oim.order_item_id], f"+{oim.modifier.name}")
        )
    if modifier_qty_map:
        for mr in ModifierRecipe.objects.filter(modifier_id__in=modifier_qty_map).select_related("inventory_item"):
            for qty, label in modifier_qty_map[mr.modifier_id]:
                required_qty_map[mr.inventory_item_id] += Decimal(str(mr.quantity_required)) * qty
                item_references[mr.inventory_item_id].append(label)

    if not required_qty_map:
        return

    # 2. Lock inventory items in a consistent order (by ID) to prevent deadlocks
    inventory_ids = sorted(list(required_qty_map.keys()))
    locked_items = {
        item.id: item
        for item in InventoryItem.objects.select_for_update().filter(id__in=inventory_ids)
    }

    transactions_to_create = []
    low_stock_items = []

    for inv_id, required_qty in required_qty_map.items():
        if inv_id not in locked_items:
            logger.error("[INVENTORY ERROR] Inventory item %s missing", inv_id)
            continue
            
        inv_item = locked_items[inv_id]
        
        # Soft-drain: consume up to available stock to prevent KOT crash
        # because InventoryItem has a CheckConstraint(stock >= 0)
        if inv_item.stock >= required_qty:
            qty_to_reduce = required_qty
        else:
            qty_to_reduce = inv_item.stock
            shortage = required_qty - inv_item.stock
            logger.error("[STOCK CRITICAL] %s shortage: %s units. Draining to 0.", inv_item.name, shortage)

        if qty_to_reduce > 0:
            # FIX: Calculate new_stock BEFORE the F() update.
            # After update(stock=F(...)), inv_item.stock is still the OLD value
            # on the in-memory object — using it post-update gives wrong threshold comparisons.
            new_stock = inv_item.stock - qty_to_reduce

            # Update with F() to avoid race conditions on concurrent requests
            InventoryItem.objects.filter(pk=inv_id).update(stock=F("stock") - qty_to_reduce)

            # Combine references
            refs = ", ".join(list(set(item_references[inv_id])))

            transactions_to_create.append(
                InventoryTransaction(
                    item=inv_item,
                    tenant=inv_item.tenant,
                    outlet=inv_item.outlet,
                    quantity=-qty_to_reduce,
                    transaction_type="consume",
                    reference=refs
                )
            )

            # Check for low stock threshold using the correctly computed new_stock
            if new_stock <= inv_item.low_stock_threshold:
                low_stock_items.append(inv_item)

    if transactions_to_create:
        InventoryTransaction.objects.bulk_create(transactions_to_create)

    # Defer notifications and PO generation to after the transaction commits
    # so that side-effects never fire for rolled-back stock changes.
    # new_stock_map holds the pre-computed post-deduction stock per item id.
    if low_stock_items:
        new_stock_map = {
            inv_id: inv.stock - required_qty_map[inv_id]
            for inv_id, inv in locked_items.items()
            if inv_id in required_qty_map
        }

        def trigger_low_stock_alerts():
            from notifications.services.notification_service import create_notification
            for item in low_stock_items:
                computed_new_stock = new_stock_map.get(item.id, item.stock)
                create_notification(
                    item.tenant,
                    item.outlet,
                    "low_stock",
                    f"{item.name} low stock ({computed_new_stock} {item.unit})"
                )
                if getattr(item, 'preferred_supplier', None) and item.reorder_quantity > 0:
                    try:
                        item.trigger_reorder()
                    except Exception as e:
                        logger.error("Failed to auto-reorder %s: %s", item.name, e)

        transaction.on_commit(trigger_low_stock_alerts)


# -----------------------------------------------------
# OPTIONAL HELPER
# -----------------------------------------------------

def check_inventory_availability(menu_item, quantity=1):
    """
    Warn-only inventory check — never blocks an order.
    KOT deduction already handles soft-drain to 0 gracefully,
    so blocking here would be stricter than the deduction allows.
    """
    recipes_manager = getattr(menu_item, "recipes", None)
    if recipes_manager is None:
        return True

    for recipe in recipes_manager.all():
        required = recipe.quantity_required * quantity
        try:
            inventory = InventoryItem.objects.get(
                id=recipe.inventory_item_id,
                tenant=menu_item.tenant,
                outlet=menu_item.outlet,
            )
            if inventory.stock < required:
                logger.warning(
                    "[INVENTORY WARN] %s: need %.2f %s, have %.2f — order allowed, will soft-drain at KOT",
                    inventory.name, required, inventory.unit, inventory.stock,
                )
        except InventoryItem.DoesNotExist:
            logger.warning(
                "[INVENTORY WARN] Recipe for '%s' references missing inventory item id=%s — skipping check",
                menu_item.name, recipe.inventory_item_id,
            )

    return True