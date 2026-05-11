from core.decorators import tenant_required
# menu/views.py
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse

from .models import MenuCategory,MenuItem
from orders.models import Table, WaiterCall
from django.http import HttpResponseForbidden
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
import json
from decimal import Decimal
from django.db import transaction
import logging

logger = logging.getLogger("pos.menu")


from .models import MenuItemModifierGroup, ModifierGroup, Modifier

from inventory.models import InventoryItem, Recipe
from setup.models import KitchenStation



def menu_view(request, qr_token):
    """
    Renders the Digital Menu for customers scanning a QR code.
    Identifies the table, outlet, and tenant via the secure qr_token.
    """

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
    """
    Allows a customer to ping a waiter from the Digital Menu.
    Implements a 60-second rate limit per table to prevent spam.
    """
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
@tenant_required
def menu_management(request):
    """
    Renders the Menu Management dashboard for Owners and Managers.
    Provides full CRUD operations for categories, items, and inventory recipes.
    """

    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()

    categories = (
        MenuCategory.objects
        .filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            is_active=True
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
    
    template_name = "menu/menu_management.html"
    if request.user.tenant.tenant_type in ['franchise', 'cafe']:
        template_name = "menu/qsr_menu_management.html"

    return render(
        request,
        template_name,
        {
            "categories": categories,
            "inventory": inventory,
            "stations": stations
        }
    )
    


@login_required
@tenant_required
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
@tenant_required
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
@tenant_required
@require_POST
def create_menu_item(request):

    try:
        # Check if request is JSON or Form Data
        if request.content_type == "application/json":
            data = json.loads(request.body)
            name = data.get("name")
            price = data.get("price")
            category_id = data.get("category")
            station_id = data.get("station")
            description = data.get("description", "")
            prep_time = data.get("estimated_prep_time", 15)
            is_veg = str(data.get("is_veg", "true")).lower() == "true"
            image = None
        else:
            name = request.POST.get("name")
            price = request.POST.get("price")
            category_id = request.POST.get("category")
            station_id = request.POST.get("station")
            description = request.POST.get("description", "")
            prep_time = request.POST.get("estimated_prep_time", 15)
            is_veg = str(request.POST.get("is_veg", "true")).lower() == "true"
            image = request.FILES.get("image")

        if not name or not price:
            return JsonResponse({"error": "Missing fields"}, status=400)

        try:
            prep_time = max(1, int(prep_time))
        except (ValueError, TypeError):
            prep_time = 15

        category = get_object_or_404(
            MenuCategory,
            id=category_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

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
            description=description,
            image=image,
            category=category,
            station=station,
            estimated_prep_time=prep_time,
            is_veg=is_veg
        )
        
        logger.info(f"User {request.user.username} created item '{name}' (Rs.{price}, prep: {prep_time}m) in category '{category.name}')")

        return JsonResponse({"success": True})

    except Exception as e:
        logger.error(f"Error creating menu item: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@tenant_required
@require_POST
def update_menu_item(request, item_id):
    """
    Comprehensive update for a menu item.
    """
    try:
        item = get_object_or_404(
            MenuItem, 
            id=item_id, 
            tenant=request.user.tenant, 
            outlet=request.user.outlet
        )

        name = request.POST.get("name")
        price = request.POST.get("price")
        category_id = request.POST.get("category")
        station_id = request.POST.get("station")
        description = request.POST.get("description", "")
        is_veg = str(request.POST.get("is_veg", "true")).lower() == "true"
        image = request.FILES.get("image")

        if not name or not price or not category_id:
            return JsonResponse({"error": "Missing required fields"}, status=400)

        category = get_object_or_404(
            MenuCategory,
            id=category_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        station = None
        if station_id:
            station = KitchenStation.objects.get(
                id=station_id,
                tenant=request.user.tenant,
                outlet=request.user.outlet
            )

        # Update fields
        item.name = name
        item.price = Decimal(price)
        item.category = category
        item.station = station
        item.description = description
        item.is_veg = is_veg
        item.description = description

        prep_time = request.POST.get("estimated_prep_time")
        if prep_time:
            try:
                item.estimated_prep_time = max(1, int(prep_time))
            except (ValueError, TypeError):
                pass
        
        if image:
            item.image = image
            
        item.save()

        logger.info(f"User {request.user.username} updated item ID {item_id} - '{name}' (prep: {item.estimated_prep_time}m)")
        return JsonResponse({"success": True})

    except Exception as e:
        logger.error(f"Error updating menu item: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)



@login_required
@tenant_required
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
@tenant_required
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
@tenant_required
@require_POST
def update_price(request, item_id):

    try:

        data = json.loads(request.body)

        from decimal import Decimal, InvalidOperation
        try:
            price = Decimal(str(data.get("price")))
            if price < 0:
                return JsonResponse({"error": "Invalid price"}, status=400)
        except InvalidOperation:
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
@tenant_required
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
@tenant_required
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
@tenant_required
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
@tenant_required
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
@tenant_required
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
            "message": f"In-build AI successfully parsed and imported {imported_count} items."
        })

    except Exception as e:
        logger.exception("AI Import Error")
        return JsonResponse({"error": str(e)}, status=400)


def digital_menu(request):
    """Customer-facing menu for self-ordering via QR."""
    from django.http import Http404
    from orders.models import Table
    
    table_token = request.GET.get("table_token")
    table_id = request.GET.get("table")
    table = None
    if table_token:
        table = Table.objects.filter(qr_token=table_token).first()
    elif table_id:
        table = Table.objects.filter(id=table_id).first()

    # Determine tenant/outlet context
    if table:
        tenant = table.tenant
        outlet = table.outlet
    elif request.user.is_authenticated:
        tenant = request.user.tenant
        outlet = request.user.outlet
    else:
        # No table token and not logged in — refuse to serve random tenant data
        raise Http404("No valid table token provided.")

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


@login_required
@tenant_required
def modifier_management(request):
    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()

    groups = ModifierGroup.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).prefetch_related("modifiers")

    items = MenuItem.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    # Get linked mappings
    linked_mappings = MenuItemModifierGroup.objects.filter(
        menu_item__tenant=request.user.tenant,
        menu_item__outlet=request.user.outlet
    ).select_related("menu_item", "modifier_group")

    return render(
        request,
        "menu/modifiers_management.html",
        {
            "groups": groups,
            "items": items,
            "linked_mappings": linked_mappings
        }
    )


@login_required
@tenant_required
@require_POST
def create_modifier_group(request):
    try:
        data = json.loads(request.body)
        name = data.get("name")
        is_required = data.get("is_required", False)
        max_select = int(data.get("max_select", 1))

        if not name:
            return JsonResponse({"error": "Group name required"}, status=400)

        ModifierGroup.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name,
            is_required=is_required,
            max_select=max_select
        )
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@require_POST
def delete_modifier_group(request, group_id):
    group = get_object_or_404(
        ModifierGroup,
        id=group_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )
    group.delete()
    return JsonResponse({"success": True})


@login_required
@tenant_required
@require_POST
def add_modifier(request):
    try:
        data = json.loads(request.body)
        group_id = data.get("group_id")
        name = data.get("name")
        price = data.get("price", 0)

        group = get_object_or_404(
            ModifierGroup,
            id=group_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        Modifier.objects.create(
            group=group,
            name=name,
            price=price
        )
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@require_POST
def delete_modifier(request, modifier_id):
    modifier = get_object_or_404(
        Modifier,
        id=modifier_id,
        group__tenant=request.user.tenant,
        group__outlet=request.user.outlet
    )
    modifier.delete()
    return JsonResponse({"success": True})


@login_required
@tenant_required
@require_POST
def link_modifier_group(request):
    try:
        data = json.loads(request.body)
        item_id = data.get("item_id")
        group_id = data.get("group_id")

        item = get_object_or_404(MenuItem, id=item_id, tenant=request.user.tenant, outlet=request.user.outlet)
        group = get_object_or_404(ModifierGroup, id=group_id, tenant=request.user.tenant, outlet=request.user.outlet)

        MenuItemModifierGroup.objects.get_or_create(
            menu_item=item,
            modifier_group=group
        )
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@require_POST
def unlink_modifier_group(request):
    try:
        data = json.loads(request.body)
        item_id = data.get("item_id")
        group_id = data.get("group_id")

        mapping = get_object_or_404(
            MenuItemModifierGroup,
            menu_item_id=item_id,
            modifier_group_id=group_id,
            menu_item__tenant=request.user.tenant,
            menu_item__outlet=request.user.outlet
        )
        mapping.delete()
        return JsonResponse({"success": True})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
def gst_management(request):
    """
    GST rate management page — shows all items grouped by category
    with their current GST rates.
    """
    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()

    categories = MenuCategory.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).prefetch_related("items").order_by("display_order") if hasattr(MenuCategory, 'display_order') else MenuCategory.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).prefetch_related("items")

    GST_RATES = [
        {"value": "0.00",  "label": "0% — Exempt"},
        {"value": "5.00",  "label": "5% — Non-AC Restaurant"},
        {"value": "12.00", "label": "12% — Packaged Food"},
        {"value": "18.00", "label": "18% — AC / Liquor License"},
    ]

    return render(request, "menu/gst_management.html", {
        "categories": categories,
        "gst_rates": GST_RATES,
    })


@login_required
@tenant_required
@require_POST
def update_item_gst(request, item_id):
    """
    AJAX endpoint — update GST rate for a single menu item.
    """
    if request.user.role not in ["owner", "manager"]:
        return JsonResponse({"error": "Permission denied"}, status=403)

    item = get_object_or_404(
        MenuItem,
        id=item_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    try:
        data = json.loads(request.body)
        gst = Decimal(str(data.get("gst_percentage", "5.00")))

        valid = [Decimal("0"), Decimal("5"), Decimal("12"), Decimal("18")]
        if gst not in valid:
            return JsonResponse({"error": "Invalid GST rate"}, status=400)

        item.gst_percentage = gst
        item.save(update_fields=["gst_percentage"])

        logger.info(f"User {request.user.username} updated GST for item '{item.name}' to {gst}%")

        return JsonResponse({
            "success": True,
            "item_id": item.id,
            "gst_percentage": str(gst),
            "message": f"{item.name} GST updated to {gst}%"
        })

    except (json.JSONDecodeError, Exception) as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@require_POST
def update_category_gst(request, category_id):
    """
    AJAX endpoint — update GST rate for ALL items in a category.
    """
    if request.user.role not in ["owner", "manager"]:
        return JsonResponse({"error": "Permission denied"}, status=403)

    category = get_object_or_404(
        MenuCategory,
        id=category_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    try:
        data = json.loads(request.body)
        gst = Decimal(str(data.get("gst_percentage", "5.00")))

        valid = [Decimal("0"), Decimal("5"), Decimal("12"), Decimal("18")]
        if gst not in valid:
            return JsonResponse({"error": "Invalid GST rate"}, status=400)

        updated = category.items.filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet
        ).update(gst_percentage=gst)

        logger.info(f"User {request.user.username} bulk-updated GST for category '{category.name}' to {gst}% ({updated} items)")

        return JsonResponse({
            "success": True,
            "category_id": category.id,
            "gst_percentage": str(gst),
            "updated_count": updated,
            "message": f"{updated} items in {category.name} updated to {gst}%"
        })

    except (json.JSONDecodeError, Exception) as e:
        return JsonResponse({"error": str(e)}, status=400)

@login_required
@tenant_required
@require_POST
def sync_menu_to_outlets(request):
    """
    Pushes all categories, items, and recipe mappings from the current source outlet
    to all other QSR outlets in the same tenant.
    Updates the price if the item already exists.
    """
    from tenants.models import Outlet
    from inventory.models import InventoryItem
    
    if request.user.role != "owner":
        return JsonResponse({"error": "Only owners can sync menus"}, status=403)

    tenant = request.user.tenant
    source_outlet = request.user.outlet

    target_outlets = Outlet.objects.filter(
        tenant=tenant
    ).exclude(id=source_outlet.id)

    if not target_outlets.exists():
        return JsonResponse({"error": "No other QSR branches found to sync to."}, status=400)

    source_categories = MenuCategory.objects.filter(tenant=tenant, outlet=source_outlet)
    
    stats = {"categories_created": 0, "items_created": 0, "recipes_created": 0, "outlets_updated": target_outlets.count()}

    with transaction.atomic():
        for target_outlet in target_outlets:
            for src_cat in source_categories:
                target_cat, cat_created = MenuCategory.objects.update_or_create(
                    tenant=tenant,
                    outlet=target_outlet,
                    name=src_cat.name,
                    defaults={
                        "display_order": src_cat.display_order,
                        "is_active": src_cat.is_active
                    }
                )
                if cat_created: stats["categories_created"] += 1

                for src_item in src_cat.items.all():
                    target_item, item_created = MenuItem.objects.update_or_create(
                        tenant=tenant,
                        outlet=target_outlet,
                        name=src_item.name,
                        defaults={
                            "category": target_cat,
                            "price": src_item.price,
                            "description": src_item.description,
                            "estimated_prep_time": src_item.estimated_prep_time,
                            "gst_percentage": src_item.gst_percentage,
                            "is_available": src_item.is_available,
                            "available_takeaway": src_item.available_takeaway,
                            "available_zomato": src_item.available_zomato,
                            "available_swiggy": src_item.available_swiggy,
                            "is_veg": src_item.is_veg
                        }
                    )
                    if item_created: stats["items_created"] += 1

                    for src_recipe in src_item.recipes.all():
                        target_inv = InventoryItem.objects.filter(
                            tenant=tenant,
                            outlet=target_outlet,
                            name__iexact=src_recipe.inventory_item.name
                        ).first()

                        if target_inv:
                            from menu.models import Recipe
                            _, recipe_created = Recipe.objects.get_or_create(
                                menu_item=target_item,
                                inventory_item=target_inv,
                                defaults={
                                    "quantity_required": src_recipe.quantity_required
                                }
                            )
                            if recipe_created: stats["recipes_created"] += 1

    return JsonResponse({"success": True, "stats": stats})


