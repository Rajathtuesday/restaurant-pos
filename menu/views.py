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



from inventory.models import InventoryItem, Recipe




def menu_view(request, qr_token):

    table = get_object_or_404(Table, qr_token=qr_token)

    categories = MenuCategory.objects.filter(
        tenant=table.tenant,
        outlet=table.outlet,
        is_active=True
    ).prefetch_related("items")

    return render(request, "menu/menu.html", {
        "table": table,
        "categories": categories
    })


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

    categories = (MenuCategory.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).prefetch_related("items", "items__recipes", "items__recipes__inventory_item")
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

    data = json.loads(request.body)

    name = data.get("name")

    if not name:
        return JsonResponse({"error":"Name required"},status=400)

    MenuCategory.objects.create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        name=name
    )

    return JsonResponse({"success":True})




@login_required
@require_POST
def create_menu_item(request):

    data = json.loads(request.body)

    name = data.get("name")
    price = data.get("price")
    category_id = data.get("category")

    category = MenuCategory.objects.get(
        id=category_id,
        tenant=request.user.tenant
    )

    MenuItem.objects.create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        name=name,
        price=price,
        category=category
    )

    return JsonResponse({"success":True})



@login_required
@require_POST
def add_recipe(request):

    data = json.loads(request.body)

    item_id = data.get("menu_item")
    inventory_id = data.get("inventory_item")
    quantity = data.get("quantity")

    menu_item = MenuItem.objects.get(
        id=item_id,
        tenant=request.user.tenant
    )

    inventory = InventoryItem.objects.get(
        id=inventory_id,
        tenant=request.user.tenant
    )

    Recipe.objects.create(
        menu_item=menu_item,
        inventory_item=inventory,
        quantity_required=quantity
    )

    return JsonResponse({"success": True})


@login_required
@require_POST
def delete_menu_item(request, item_id):

    item = MenuItem.objects.get(
        id=item_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    item.delete()

    return JsonResponse({"success": True})



@login_required
@require_POST
def update_price(request, item_id):

    data = json.loads(request.body)

    price = data.get("price")

    item = MenuItem.objects.get(
        id=item_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    item.price = price
    item.save(update_fields=["price"])

    return JsonResponse({"success": True})


@login_required
@require_POST
def toggle_item(request, item_id):

    item = MenuItem.objects.get(
        id=item_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    item.is_available = not item.is_available
    item.save(update_fields=["is_available"])

    return JsonResponse({"success": True})

