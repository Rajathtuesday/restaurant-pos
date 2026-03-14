# menu/views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse

import inventory
from .models import MenuCategory,MenuItem
from orders.models import Table, WaiterCall
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
import json


from .models import MenuItemModifierGroup

from inventory.models import InventoryItem, Recipe




def menu_view(request, qr_token):

    table = get_object_or_404(Table, qr_token=qr_token)

    categories = (
        MenuCategory.objects
        .filter(
            tenant=table.tenant,
            outlet=table.outlet,
            is_active=True
        )
        .prefetch_related(
            "items",
            "items__modifier_groups__modifier_group__modifiers"
        )
    )

    return render(
        request,
        "menu/menu.html",
        {
            "table": table,
            "categories": categories
        }
    )


def call_waiter(request, qr_token):

    table = get_object_or_404(Table, qr_token=qr_token)

    WaiterCall.objects.create(
        tenant=table.tenant,
        outlet=table.outlet,
        table=table
    )

    return JsonResponse({"success": True})


@login_required
def menu_management(request):

    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()

    categories = (
        MenuCategory.objects
        .filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        .prefetch_related(
            "items",
            "items__recipes",
            "items__recipes__inventory_item"
        )
    )

    inventory = InventoryItem.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    return render(
        request,
        "menu/menu_management.html",
        {
            "categories": categories,
            "inventory": inventory
        }
    )
    


@login_required
@require_POST
def create_category(request):

    try:

        data = json.loads(request.body)

        name = data.get("name")

        if not name:
            return JsonResponse({"error": "Category name required"}, status=400)

        MenuCategory.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name
        )

        return JsonResponse({"success": True})

    except Exception as e:

        return JsonResponse({"error": str(e)}, status=500)



@login_required
@require_POST
def create_menu_item(request):

    try:

        data = json.loads(request.body)

        name = data.get("name")
        price = data.get("price")
        category_id = data.get("category")

        if not name or not price:
            return JsonResponse({"error": "Missing fields"}, status=400)

        category = get_object_or_404(
            MenuCategory,
            id=category_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        MenuItem.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name,
            price=price,
            category=category
        )

        return JsonResponse({"success": True})

    except Exception as e:

        return JsonResponse({"error": str(e)}, status=500)



@login_required
@require_POST
def add_recipe(request):

    try:

        data = json.loads(request.body)

        item_id = data.get("menu_item")
        inventory_id = data.get("inventory_item")
        quantity = data.get("quantity")

        if not quantity:
            return JsonResponse({"error": "Quantity required"}, status=400)

        menu_item = get_object_or_404(
            MenuItem,
            id=item_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        inventory = get_object_or_404(
            InventoryItem,
            id=inventory_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        recipe, created = Recipe.objects.get_or_create(
            menu_item=menu_item,
            inventory_item=inventory,
            defaults={"quantity_required": quantity}
        )

        if not created:
            recipe.quantity_required = quantity
            recipe.save(update_fields=["quantity_required"])

        return JsonResponse({"success": True})

    except Exception as e:

        return JsonResponse({"error": str(e)}, status=500)
    
    

@login_required
@require_POST
def delete_menu_item(request, item_id):

    item = get_object_or_404(
        MenuItem,
        id=item_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    item.delete()

    return JsonResponse({"success": True})


@login_required
@require_POST
def update_price(request, item_id):

    try:

        data = json.loads(request.body)

        price = float(data.get("price"))

        if price < 0:
            return JsonResponse({"error": "Invalid price"}, status=400)

        item = get_object_or_404(
            MenuItem,
            id=item_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        item.price = price
        item.save(update_fields=["price"])

        return JsonResponse({"success": True})

    except Exception as e:

        return JsonResponse({"error": str(e)}, status=500)
    

@login_required
@require_POST
def toggle_item(request, item_id):

    item = get_object_or_404(
        MenuItem,
        id=item_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    item.is_available = not item.is_available

    item.save(update_fields=["is_available"])

    return JsonResponse({"success": True})


@login_required
def menu_item_modifiers(request, item_id):

    item = get_object_or_404(
        MenuItem,
        id=item_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    groups = (
        MenuItemModifierGroup.objects
        .filter(menu_item=item)
        .select_related("modifier_group")
        .prefetch_related("modifier_group__modifiers")
    )

    data = []

    for g in groups:

        group = g.modifier_group

        modifiers = []

        for m in group.modifiers.filter(is_active=True):

            modifiers.append({
                "id": m.id,
                "name": m.name,
                "price": float(m.price)
            })

        data.append({
            "group_name": group.name,
            "is_required": group.is_required,
            "max_select": group.max_select,
            "modifiers": modifiers
        })

    return JsonResponse({"groups": data})