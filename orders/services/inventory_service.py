# orders/services/inventory_service.py

from django.db.models import F
from inventory.models import InventoryItem

def deduct_inventory(order_item):

    for recipe in order_item.menu_item.recipes.all():

        inventory = recipe.inventory_item

        required = recipe.quantity_required * order_item.quantity

        if inventory.stock >= required:

            inventory.stock -= required

        else:

            shortage = required - inventory.stock

            print(
                f"⚠ STOCK SHORTAGE: {inventory.name} "
                f"(missing {shortage})"
            )

            inventory.stock = 0

        inventory.save(update_fields=["stock"])
        
        
@property
def is_low_stock(self):
    return self.stock <= self.low_stock_threshold