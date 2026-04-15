from decimal import Decimal
import logging
from .models import InventoryItem, Recipe

logger = logging.getLogger("pos.inventory")

def deduct_stock_for_item(order_item):
    """
    Deducts inventory items based on the recipe of a menu item.
    Called when an item is served or sent to kitchen.
    """
    menu_item = order_item.menu_item
    quantity = order_item.quantity
    
    recipes = Recipe.objects.filter(menu_item=menu_item).select_related("inventory_item")
    
    for recipe in recipes:
        total_deduction = recipe.quantity_required * Decimal(quantity)
        try:
            recipe.inventory_item.reduce_stock(
                quantity=total_deduction,
                reference=f"Order #{order_item.order.order_number} | {menu_item.name}"
            )
        except Exception as e:
            logger.error(f"Failed to deduct stock for {recipe.inventory_item.name}: {e}")
            # In a real POS, we might want to flag this to the manager
            # but we don't block the order flow usually unless strict mode is on.
