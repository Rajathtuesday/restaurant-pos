# inventory/recipe_service.py
"""
The single validated write path for MenuItem->InventoryItem and
Modifier->InventoryItem recipe links. Both the manual "add ingredient" views
(menu/views/item_views.py::add_recipe, menu/views/modifier_views.py::
add_modifier_recipe) and the AI recipe importer's confirm step call these
functions — so the two paths can never silently drift apart on validation.
"""
from inventory.models import InventoryItem, ModifierRecipe, Recipe
from inventory.unit_conversion import units_compatible


class RecipeUnitMismatchError(Exception):
    """Raised when a posted unit can't be converted to the inventory item's
    tracked unit (different measurement families — e.g. volume vs weight)."""


class RecipeCrossTenantError(Exception):
    """Raised when the menu item / modifier and the inventory item don't
    belong to the same tenant."""


def upsert_recipe(menu_item, inventory_item, quantity, unit=None):
    """
    Create or update the Recipe linking menu_item to inventory_item.

    unit=None keeps whatever unit an existing Recipe row already has, or
    defaults to the inventory item's own unit for a new row — a bare
    quantity-only edit must never silently reset a deliberately different
    unit back to the default.
    """
    if menu_item.tenant_id != inventory_item.tenant_id:
        raise RecipeCrossTenantError(
            f"'{inventory_item.name}' belongs to a different tenant than '{menu_item.name}'."
        )

    if unit and not units_compatible(unit, inventory_item.unit):
        raise RecipeUnitMismatchError(
            f"'{unit}' can't be converted to '{inventory_item.unit}' "
            f"({inventory_item.name}'s tracked unit) — they measure different things."
        )

    existing = Recipe.objects.filter(menu_item=menu_item, inventory_item=inventory_item).first()
    if existing:
        existing.quantity_required = quantity
        if unit:
            existing.unit = unit
        existing.save(update_fields=["quantity_required", "unit"])
        return existing, False

    recipe = Recipe.objects.create(
        menu_item=menu_item, inventory_item=inventory_item,
        quantity_required=quantity, unit=unit or inventory_item.unit,
    )
    return recipe, True


def upsert_modifier_recipe(modifier, inventory_item, quantity, unit=None):
    """Same as upsert_recipe, for Modifier -> InventoryItem links.

    ModifierRecipe has no model-level clean() cross-tenant check today (a
    pre-existing gap — Recipe has one, this doesn't) so it's enforced here
    instead, same as the unit check.
    """
    if modifier.group.tenant_id != inventory_item.tenant_id:
        raise RecipeCrossTenantError(
            f"'{inventory_item.name}' belongs to a different tenant than '{modifier.name}'."
        )

    if unit and not units_compatible(unit, inventory_item.unit):
        raise RecipeUnitMismatchError(
            f"'{unit}' can't be converted to '{inventory_item.unit}' "
            f"({inventory_item.name}'s tracked unit) — they measure different things."
        )

    existing = ModifierRecipe.objects.filter(modifier=modifier, inventory_item=inventory_item).first()
    if existing:
        existing.quantity_required = quantity
        if unit:
            existing.unit = unit
        existing.save(update_fields=["quantity_required", "unit"])
        return existing, False

    modifier_recipe = ModifierRecipe.objects.create(
        modifier=modifier, inventory_item=inventory_item,
        quantity_required=quantity, unit=unit or inventory_item.unit,
    )
    return modifier_recipe, True
