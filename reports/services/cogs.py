# reports/services/cogs.py
"""
Per-menu-item COGS (cost of goods sold) computation — extracted from
pl_reports.py::gross_margin_report so the same recipe/modifier costing logic
can be reused by menu_engineering.py without duplicating it. This is a pure
refactor of that function's inline loop; gross_margin_report's own numbers
must not change as a result of the extraction.
"""
from decimal import Decimal

from inventory.models import Recipe
from inventory.unit_conversion import convert_quantity, IncompatibleUnitsError
import logging

logger = logging.getLogger("pos.reports")


def item_cogs_map(order_items_queryset):
    """
    Computes COGS for the given OrderItem queryset, covering both
    recipe-linked and modifier-linked inventory costs (with unit conversion
    and IncompatibleUnitsError handling identical to the original inline
    loop this was extracted from).

    Returns (cogs_map, items_with_recipe, items_without_recipe):
      cogs_map            -- {menu_item_id: total_cogs (Decimal)}, one entry
                              per menu item that had at least one costed
                              recipe/modifier line. A menu_item_id NOT present
                              in this dict means its cost is entirely unknown
                              (no recipe, no costed modifier) -- callers
                              should treat that as "cost unknown", not zero.
      items_with_recipe    -- count of OrderItem lines that had at least one
                              recipe or modifier-inventory link.
      items_without_recipe -- count of OrderItem lines that had neither.
    """
    item_qs = order_items_queryset.select_related("menu_item").prefetch_related(
        "modifiers__modifier__inventory_links__inventory_item"
    )

    cogs_map = {}
    items_with_recipe = 0
    items_without_recipe = 0

    recipe_map = {}
    for recipe in Recipe.objects.filter(
        menu_item__in=item_qs.values("menu_item_id")
    ).select_related("inventory_item"):
        recipe_map.setdefault(recipe.menu_item_id, []).append(recipe)

    for item in item_qs.iterator(chunk_size=200):
        recipes = recipe_map.get(item.menu_item_id, [])
        # Modifier-linked inventory (e.g. "Extra Cheese") contributes to COGS
        # too -- a dish sold with a costed modifier is undercosted (and its
        # margin overstated) if only the base recipe is counted.
        modifier_links = [
            (oim.modifier, mr)
            for oim in item.modifiers.all() if oim.modifier
            for mr in oim.modifier.inventory_links.all()
        ]
        if recipes or modifier_links:
            items_with_recipe += 1
            item_cost = cogs_map.get(item.menu_item_id, Decimal("0"))
            for recipe in recipes:
                cost = recipe.inventory_item.cost_price  # Rs per inventory-item unit
                qty_recipe_unit = item.quantity * recipe.quantity_required
                try:
                    # cost_price is per the INVENTORY ITEM's unit, so the
                    # quantity must be in that same unit before multiplying —
                    # a recipe in grams against an item costed per kilogram
                    # would otherwise overstate COGS by 1000x.
                    qty = convert_quantity(qty_recipe_unit, recipe.unit, recipe.inventory_item.unit)
                except IncompatibleUnitsError as e:
                    logger.warning(
                        "[UNIT MISMATCH] Recipe %s -> '%s': %s. Excluding this "
                        "recipe line from COGS rather than compute a wrong cost.",
                        recipe.id, recipe.inventory_item.name, e,
                    )
                    continue
                item_cost += Decimal(str(cost)) * Decimal(str(qty))
            for modifier, mod_recipe in modifier_links:
                cost = mod_recipe.inventory_item.cost_price
                qty_recipe_unit = item.quantity * mod_recipe.quantity_required
                try:
                    qty = convert_quantity(qty_recipe_unit, mod_recipe.unit, mod_recipe.inventory_item.unit)
                except IncompatibleUnitsError as e:
                    logger.warning(
                        "[UNIT MISMATCH] ModifierRecipe %s -> '%s': %s. Excluding "
                        "this line from COGS rather than compute a wrong cost.",
                        mod_recipe.id, mod_recipe.inventory_item.name, e,
                    )
                    continue
                item_cost += Decimal(str(cost)) * Decimal(str(qty))
            cogs_map[item.menu_item_id] = item_cost
        else:
            items_without_recipe += 1

    return cogs_map, items_with_recipe, items_without_recipe
