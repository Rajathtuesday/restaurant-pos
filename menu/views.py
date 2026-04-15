# menu/views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse

from .models import MenuCategory,MenuItem
from orders.models import Table, WaiterCall
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
import json
from django.db import transaction
import logging

logger = logging.getLogger("pos.menu")


from .models import MenuItemModifierGroup

from inventory.models import InventoryItem, Recipe
from setup.models import KitchenStation



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
    from django.utils import timezone
    from datetime import timedelta

    table = get_object_or_404(Table, qr_token=qr_token)

    # ✅ Rate limit: one call per table every 60 seconds
    recent = WaiterCall.objects.filter(
        table=table,
        is_resolved=False,
        created_at__gte=timezone.now() - timedelta(seconds=60)
    ).exists()

    if recent:
        return JsonResponse({"error": "A waiter has already been called. Please wait."}, status=429)

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
        outlet=request.user.outlet,
        
    )
    
    stations = KitchenStation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    )
    
    

    return render(
        request,
        "menu/menu_management.html",
        {
            "categories": categories,
            "inventory": inventory,
            "stations": stations
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
        
        logger.info(f"User {request.user.username} created category '{name}' for outlet {request.user.outlet.name}")

        return JsonResponse({"success": True})

    except Exception as e:
        logger.error(f"Error creating category: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
def delete_category(request, category_id):
    """
    Deletes a category and all its attached items.
    """
    try:
        category = get_object_or_404(
            MenuCategory,
            id=category_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        
        name = category.name
        category.delete()
        
        logger.warning(f"User {request.user.username} deleted category '{name}' and all its items")
        
        return JsonResponse({"success": True})

    except Exception as e:
        logger.error(f"Error deleting category: {str(e)}")
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

        station_id = data.get("station")

        station = None
        if station_id:
            station = KitchenStation.objects.get(
                id=station_id,
                tenant=request.user.tenant,
                outlet=request.user.outlet
            )

        MenuItem.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name,
            price=price,
            category=category,
            station=station
        )
        
        logger.info(f"User {request.user.username} created item '{name}' (₹{price}) in category '{category.name}'")

        return JsonResponse({"success": True})

    except Exception as e:
        logger.error(f"Error creating menu item: {str(e)}")
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
    
    item_name = item.name
    item.delete()
    
    logger.warning(f"User {request.user.username} deleted menu item '{item_name}'")

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
    

    return JsonResponse({"success": True})


@login_required
@require_POST
def toggle_item(request, item_id):
    """Toggle global visibility of an item."""
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
@require_POST
def toggle_platform_availability(request, item_id):
    """Toggle item availability on specific platforms (takeaway, zomato, swiggy)."""
    try:
        data = json.loads(request.body)
        platform = data.get("platform")  # "takeaway", "zomato", "swiggy"
        
        item = get_object_or_404(
            MenuItem,
            id=item_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        if platform == "takeaway":
            item.available_takeaway = not item.available_takeaway
        elif platform == "zomato":
            item.available_zomato = not item.available_zomato
        elif platform == "swiggy":
            item.available_swiggy = not item.available_swiggy
        else:
            return JsonResponse({"error": "Invalid platform"}, status=400)

        item.save()
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


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


@login_required
@require_POST
def update_station(request, item_id):

    try:
        data = json.loads(request.body)
        station_id = data.get("station")

        item = MenuItem.objects.get(
            id=item_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        if station_id:
            station = KitchenStation.objects.get(
                id=station_id,
                tenant=request.user.tenant,
                outlet=request.user.outlet
            )
            item.station = station
        else:
            item.station = None

        item.save(update_fields=["station"])

        return JsonResponse({"success": True})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
def ai_menu_importer(request):
    """
    Upgraded AI Parser: Uses Gemini to 'see' images or parse text into menu items.
    Supports multipart/form-data for image/pdf uploads.
    """
    from core.ai_service import AIService
    from decimal import Decimal

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        text = request.POST.get("text")
        file = request.FILES.get("file")
        
        ai = AIService()
        
        image_bytes = None
        mime_type = None
        if file:
            image_bytes = file.read()
            mime_type = file.content_type

        # Call Gemini via our service
        structured_data = ai.parse_menu(text=text, image_bytes=image_bytes, mime_type=mime_type)
        
        imported_count = 0
        with transaction.atomic():
            for entry in structured_data:
                category_name = entry.get("category", "General")
                
                category, _ = MenuCategory.objects.get_or_create(
                    tenant=request.user.tenant,
                    outlet=request.user.outlet,
                    name=category_name
                )
                
                for item_data in entry.get("items", []):
                    name = item_data.get("name")
                    price = item_data.get("price", 0)
                    
                    if name:
                        MenuItem.objects.get_or_create(
                            tenant=request.user.tenant,
                            outlet=request.user.outlet,
                            category=category,
                            name=name,
                            defaults={"price": Decimal(str(price))}
                        )
                        imported_count += 1

        return JsonResponse({
            "success": True, 
            "message": f"Gemini AI successfully parsed and imported {imported_count} items."
        })

    except Exception as e:
        logger.exception("AI Import Error")
        return JsonResponse({"error": str(e)}, status=400)


def digital_menu(request):
    """Customer-facing menu for self-ordering via QR."""
    from orders.models import Table
    from tenants.models import Tenant, Outlet
    
    table_id = request.GET.get("table")
    table = None
    if table_id:
        table = Table.objects.filter(id=table_id).first()
        
    # Determine tenant/outlet context
    if table:
        tenant = table.tenant
        outlet = table.outlet
    elif request.user.is_authenticated:
        tenant = request.user.tenant
        outlet = request.user.outlet
    else:
        # Fallback for public browsing (take the first tenant for demo)
        tenant = Tenant.objects.first()
        outlet = Outlet.objects.first()

    categories = MenuCategory.objects.filter(
        tenant=tenant,
        outlet=outlet,
        is_active=True
    ).prefetch_related("items")

    return render(request, "menu/digital_menu.html", {
        "categories": categories,
        "table": table,
        "tenant": tenant,
        "outlet": outlet
    })