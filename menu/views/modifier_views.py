"""Modifier group and modifier CRUD, item linking."""
import json
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required
from menu.models import MenuItem, MenuItemModifierGroup, ModifierGroup, Modifier


@login_required
@tenant_required
@feature_required("modifiers")
def modifier_management(request):
    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()
    groups = (
        ModifierGroup.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet)
        .prefetch_related("modifiers")
    )
    items = MenuItem.objects.filter(tenant=request.user.tenant, outlet=request.user.outlet)
    linked_mappings = (
        MenuItemModifierGroup.objects
        .filter(menu_item__tenant=request.user.tenant, menu_item__outlet=request.user.outlet)
        .select_related("menu_item", "modifier_group")
    )
    return render(request, "menu/modifiers_management.html", {
        "groups": groups, "items": items, "linked_mappings": linked_mappings,
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
