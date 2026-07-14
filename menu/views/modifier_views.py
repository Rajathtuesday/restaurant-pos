"""Modifier group and modifier CRUD, item linking."""
import json
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required, role_required
from menu.models import MenuItem, MenuItemModifierGroup, ModifierGroup, Modifier
from inventory.models import InventoryItem, ModifierRecipe
from inventory.recipe_service import RecipeUnitMismatchError, upsert_modifier_recipe


@login_required
@tenant_required
@feature_required("modifiers")
def modifier_management(request):
    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()
    groups = (
        ModifierGroup.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet)
        .prefetch_related("modifiers", "modifiers__inventory_links__inventory_item")
    )
    items = MenuItem.objects.filter(tenant=request.user.tenant, outlet=request.user.outlet)
    linked_mappings = (
        MenuItemModifierGroup.objects
        .filter(menu_item__tenant=request.user.tenant, menu_item__outlet=request.user.outlet)
        .select_related("menu_item", "modifier_group")
    )
    inventory_items = InventoryItem.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet
    ).order_by("name")
    return render(request, "menu/modifiers_management.html", {
        "groups": groups, "items": items, "linked_mappings": linked_mappings,
        "inventory_items": inventory_items,
    })


@login_required
@tenant_required
@feature_required("modifiers")
def menu_item_modifiers(request, item_id):
    item   = get_object_or_404(MenuItem, id=item_id, tenant=request.user.tenant, outlet=request.user.outlet)
    groups = (
        MenuItemModifierGroup.objects
        .filter(menu_item=item)
        .select_related("modifier_group")
        .prefetch_related("modifier_group__modifiers")
    )
    data = []
    for g in groups:
        group = g.modifier_group
        data.append({
            "group_name":  group.name,
            "is_required": group.is_required,
            "max_select":  group.max_select,
            "modifiers": [
                {"id": m.id, "name": m.name, "price": float(m.price)}
                for m in group.modifiers.filter(is_active=True)
            ],
        })
    return JsonResponse({"groups": data})


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("modifiers")
@require_POST
def create_modifier_group(request):
    try:
        data       = json.loads(request.body)
        name       = data.get("name")
        is_required = data.get("is_required", False)
        max_select = int(data.get("max_select", 1))
        if not name:
            return JsonResponse({"error": "Group name required"}, status=400)
        ModifierGroup.objects.create(
            tenant=request.user.tenant, outlet=request.user.outlet,
            name=name, is_required=is_required, max_select=max_select,
        )
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("modifiers")
@require_POST
def delete_modifier_group(request, group_id):
    group = get_object_or_404(
        ModifierGroup, id=group_id, tenant=request.user.tenant, outlet=request.user.outlet
    )
    group.delete()
    return JsonResponse({"success": True})


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("modifiers")
@require_POST
def add_modifier(request):
    try:
        data     = json.loads(request.body)
        group_id = data.get("group_id")
        name     = data.get("name")
        price    = data.get("price", 0)
        group    = get_object_or_404(
            ModifierGroup, id=group_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
        Modifier.objects.create(group=group, name=name, price=price)
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("modifiers")
@require_POST
def delete_modifier(request, modifier_id):
    modifier = get_object_or_404(
        Modifier, id=modifier_id,
        group__tenant=request.user.tenant, group__outlet=request.user.outlet,
    )
    modifier.delete()
    return JsonResponse({"success": True})


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("modifiers")
@require_POST
def link_modifier_group(request):
    try:
        data     = json.loads(request.body)
        item_id  = data.get("item_id")
        group_id = data.get("group_id")
        item     = get_object_or_404(MenuItem, id=item_id, tenant=request.user.tenant, outlet=request.user.outlet)
        group    = get_object_or_404(ModifierGroup, id=group_id, tenant=request.user.tenant, outlet=request.user.outlet)
        MenuItemModifierGroup.objects.get_or_create(menu_item=item, modifier_group=group)
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("modifiers")
@require_POST
def unlink_modifier_group(request):
    try:
        data     = json.loads(request.body)
        item_id  = data.get("item_id")
        group_id = data.get("group_id")
        mapping  = get_object_or_404(
            MenuItemModifierGroup, menu_item_id=item_id, modifier_group_id=group_id,
            menu_item__tenant=request.user.tenant, menu_item__outlet=request.user.outlet,
        )
        mapping.delete()
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("modifiers")
@require_POST
def add_modifier_recipe(request, modifier_id):
    """Link a modifier to an inventory item so it deducts stock on KOT."""
    try:
        data        = json.loads(request.body)
        inv_item_id = data.get("inventory_item_id")
        quantity    = data.get("quantity")
        # None keeps an existing row's unit unchanged — a bare quantity-only
        # edit must never silently reset a deliberately different unit.
        unit        = data.get("unit")

        if not all([inv_item_id, quantity]):
            return JsonResponse({"error": "inventory_item_id and quantity are required"}, status=400)

        modifier = get_object_or_404(
            Modifier, id=modifier_id,
            group__tenant=request.user.tenant, group__outlet=request.user.outlet,
        )
        inv_item = get_object_or_404(
            InventoryItem, id=inv_item_id,
            tenant=request.user.tenant, outlet=request.user.outlet,
        )

        try:
            _, created = upsert_modifier_recipe(modifier, inv_item, quantity, unit)
        except RecipeUnitMismatchError as e:
            return JsonResponse({"error": str(e)}, status=400)

        return JsonResponse({"success": True, "created": created})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("modifiers")
@require_POST
def delete_modifier_recipe(request, modifier_id):
    """Remove the inventory link from a modifier."""
    try:
        data        = json.loads(request.body)
        inv_item_id = data.get("inventory_item_id")
        mr = get_object_or_404(
            ModifierRecipe,
            modifier_id=modifier_id,
            inventory_item_id=inv_item_id,
            modifier__group__tenant=request.user.tenant,
            modifier__group__outlet=request.user.outlet,
        )
        mr.delete()
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@feature_required("modifiers")
def modifier_inventory_links(request, modifier_id):
    """Return existing inventory links for a modifier (for the modal)."""
    modifier = get_object_or_404(
        Modifier, id=modifier_id,
        group__tenant=request.user.tenant, group__outlet=request.user.outlet,
    )
    links = ModifierRecipe.objects.filter(modifier=modifier).select_related("inventory_item")
    return JsonResponse({
        "links": [
            {
                "inventory_item_id":   l.inventory_item_id,
                "inventory_item_name": l.inventory_item.name,
                "quantity":            float(l.quantity_required),
                "unit":                l.unit,
            }
            for l in links
        ]
    })
