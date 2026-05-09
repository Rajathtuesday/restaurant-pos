
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
    
    for order_item in order_items:
        recipes_manager = getattr(order_item.menu_item, "recipes", None)
        if recipes_manager is None:
            continue
            
        recipes = recipes_manager.all()
        for recipe in recipes:
            req_qty = Decimal(str(recipe.quantity_required)) * Decimal(str(order_item.quantity))
            required_qty_map[recipe.inventory_item_id] += req_qty
            item_references[recipe.inventory_item_id].append(f"Order #{order_item.order_id}")

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
            logger.error(f"[INVENTORY ERROR] Inventory item {inv_id} missing")
            continue
            
        inv_item = locked_items[inv_id]
        
        # Soft-drain: consume up to available stock to prevent KOT crash
        # because InventoryItem has a CheckConstraint(stock >= 0)
        if inv_item.stock >= required_qty:
            qty_to_reduce = required_qty
        else:
            qty_to_reduce = inv_item.stock
            shortage = required_qty - inv_item.stock
            logger.error(f"[STOCK CRITICAL] {inv_item.name} shortage: {shortage} units. Draining to 0.")

        if qty_to_reduce > 0:
            # Update with F() to avoid stale reads
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
            
            # Check for low stock threshold manually after deducting
            new_stock = inv_item.stock - qty_to_reduce
            if new_stock <= inv_item.low_stock_threshold:
                low_stock_items.append(inv_item)

    if transactions_to_create:
        InventoryTransaction.objects.bulk_create(transactions_to_create)

    # Defer notifications and PO generation outside the DB transaction if possible,
    # or handle them lightly.
    # We use a post-commit hook to prevent holding locks during I/O
    if low_stock_items:
        from django.db import connection
        
        def trigger_low_stock_alerts():
            from notifications.services.notification_service import create_notification
            for item in low_stock_items:
                create_notification(
                    item.tenant,
                    item.outlet,
                    "low_stock",
                    f"{item.name} low stock ({item.stock} {item.unit})"
                )
                if getattr(item, 'preferred_supplier', None) and item.reorder_quantity > 0:
                    try:
                        item.trigger_reorder()
                    except Exception as e:
                        logger.error(f"Failed to auto-reorder {item.name}: {e}")

        transaction.on_commit(trigger_low_stock_alerts)


# -----------------------------------------------------
# OPTIONAL HELPER
# -----------------------------------------------------

def check_inventory_availability(menu_item, quantity=1):
    """
    Check if enough inventory exists before ordering.
    Useful for future features like:
    - blocking out-of-stock items
    - showing 'Out of stock' in POS
    """

    recipes_manager = getattr(menu_item, "recipes", None)

    if recipes_manager is None:
        return True

    recipes = recipes_manager.all()

    for recipe in recipes:

        required = recipe.quantity_required * quantity

        try:

            inventory = InventoryItem.objects.get(
                id=recipe.inventory_item_id
            )

            if inventory.stock < required:
                return False

        except InventoryItem.DoesNotExist:
            return False

    return True