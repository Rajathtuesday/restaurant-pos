
# orders/services/inventory_service.py

from django.db import transaction
import logging

logger = logging.getLogger("pos.inventory")
from django.core.exceptions import ObjectDoesNotExist

from inventory.models import InventoryItem


def deduct_inventory(order_item):
    """
    Deduct inventory for a given OrderItem based on its recipe.

    Safe behaviour:
    - If item has no recipe → skip
    - If inventory missing → skip but log
    - If stock shortage → consume remaining stock
    - Never crash KOT creation
    """

    menu_item = order_item.menu_item

    # ------------------------------------------
    # CHECK IF MENU ITEM HAS RECIPES
    # ------------------------------------------

    recipes_manager = getattr(menu_item, "recipes", None)

    if recipes_manager is None:
        # Menu item does not track inventory
        return

    recipes = recipes_manager.all()

    if not recipes.exists():
        # No recipe linked → nothing to deduct
        return

    # ------------------------------------------
    # PROCESS EACH RECIPE INGREDIENT
    # ------------------------------------------

    for recipe in recipes:

        required_quantity = recipe.quantity_required * order_item.quantity

        try:

            with transaction.atomic():

                inventory = (
                    InventoryItem.objects
                    .select_for_update()
                    .get(id=recipe.inventory_item_id)
                )

                # -------------------------------
                # NORMAL STOCK DEDUCTION
                # -------------------------------

                if inventory.stock >= required_quantity:

                    inventory.stock -= required_quantity

                else:

                    shortage = required_quantity - inventory.stock

                    logger.warning(
                        "[STOCK WARNING] %s shortage: %s units",
                        inventory.name, shortage
                    )

                    # consume remaining stock
                    inventory.stock = 0

                inventory.save(update_fields=["stock"])

        except ObjectDoesNotExist:

            logger.error(
                "[INVENTORY ERROR] Inventory item missing for recipe %s",
                recipe.id
            )

        except Exception as e:

            logger.exception(
                "[INVENTORY ERROR] deduction failed for order_item=%s: %s",
                order_item.id, str(e)
            )


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