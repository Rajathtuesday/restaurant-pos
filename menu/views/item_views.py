"""Menu item CRUD, availability toggles, station assignment."""
import json
import logging
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required, role_required
from menu.models import MenuCategory, MenuItem
from setup.models import KitchenStation
from inventory.models import InventoryItem
from inventory.recipe_service import RecipeUnitMismatchError, upsert_recipe

logger = logging.getLogger("pos.menu")


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def create_menu_item(request):
    try:
        if request.content_type == "application/json":
            data        = json.loads(request.body)
            name        = data.get("name")
            price       = data.get("price")
            category_id = data.get("category")
            station_id  = data.get("station")
            description = data.get("description", "")
            prep_time   = data.get("estimated_prep_time", 15)
            is_veg      = str(data.get("is_veg", "true")).lower() == "true"
            image       = None
        else:
            name        = request.POST.get("name")
            price       = request.POST.get("price")
            category_id = request.POST.get("category")
            station_id  = request.POST.get("station")
            description = request.POST.get("description", "")
            prep_time   = request.POST.get("estimated_prep_time", 15)
            is_veg      = str(request.POST.get("is_veg", "true")).lower() == "true"
            image       = request.FILES.get("image")

        if not name or not price:
            return JsonResponse({"error": "Missing fields"}, status=400)

        try:
            prep_time = max(1, int(prep_time))
        except (ValueError, TypeError):
            prep_time = 15

        category = get_object_or_404(
            MenuCategory, id=category_id,
            tenant=request.user.tenant, outlet=request.user.outlet
        )
        station = None
        if station_id:
            station = KitchenStation.objects.get(
                id=station_id, tenant=request.user.tenant, outlet=request.user.outlet
            )

        parcel_charge = Decimal("0")
        try:
            parcel_charge = Decimal(str(data.get("parcel_charge", 0) if request.content_type == "application/json" else request.POST.get("parcel_charge", 0) or 0))
            # Clamp negatives — a negative parcel charge would act as a stealth
            # per-item discount on every order. (update_menu_item already clamps.)
            parcel_charge = max(Decimal("0"), parcel_charge)
        except Exception:
            parcel_charge = Decimal("0")

        MenuItem.objects.create(
            tenant=request.user.tenant, outlet=request.user.outlet,
            name=name, price=price, description=description, image=image,
            category=category, station=station,
            estimated_prep_time=prep_time, is_veg=is_veg,
            parcel_charge=parcel_charge,
        )
        logger.info(
            "User %s created item '%s' (Rs.%s, prep: %sm) in category '%s'",
            request.user.username, name, price, prep_time, category.name,
        )
        return JsonResponse({"success": True})
    except Exception:
        logger.exception("Error creating menu item")
        return JsonResponse({"error": "Could not create the item. Please try again."}, status=500)


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def update_menu_item(request, item_id):
    try:
        item = get_object_or_404(
            MenuItem, id=item_id,
            tenant=request.user.tenant, outlet=request.user.outlet
        )
        name        = request.POST.get("name")
        price       = request.POST.get("price")
        category_id = request.POST.get("category")
        station_id  = request.POST.get("station")
        description = request.POST.get("description", "")
        is_veg      = str(request.POST.get("is_veg", "true")).lower() == "true"
        image       = request.FILES.get("image")

        if not name or not price or not category_id:
            return JsonResponse({"error": "Missing required fields"}, status=400)

        category = get_object_or_404(
            MenuCategory, id=category_id,
            tenant=request.user.tenant, outlet=request.user.outlet
        )
        station = None
        if station_id:
            station = KitchenStation.objects.get(
                id=station_id, tenant=request.user.tenant, outlet=request.user.outlet
            )

        item.name        = name
        item.price       = Decimal(price)
        item.category    = category
        item.station     = station
        item.description = description
        item.is_veg      = is_veg

        prep_time = request.POST.get("estimated_prep_time")
        if prep_time:
            try:
                item.estimated_prep_time = max(1, int(prep_time))
            except (ValueError, TypeError):
                pass

        parcel_charge = request.POST.get("parcel_charge")
        if parcel_charge is not None:
            try:
                item.parcel_charge = max(Decimal("0"), Decimal(str(parcel_charge)))
            except Exception:
                logger.warning(
                    "Could not parse parcel_charge=%r for item %s — left unchanged",
                    parcel_charge, item.id,
                )

        if image:
            item.image = image
        item.save()

        logger.info("User %s updated item %s '%s'", request.user.username, item_id, name)
        return JsonResponse({"success": True})
    except Exception:
        logger.exception("Error updating menu item")
        return JsonResponse({"error": "Could not update the item. Please try again."}, status=500)


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def delete_menu_item(request, item_id):
    item = get_object_or_404(
        MenuItem, id=item_id,
        tenant=request.user.tenant, outlet=request.user.outlet
    )
    name = item.name
    item.delete()
    logger.warning("User %s deleted menu item '%s'", request.user.username, name)
    return JsonResponse({"success": True})


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def update_price(request, item_id):
    try:
        data = json.loads(request.body)
        try:
            price = Decimal(str(data.get("price")))
            if price < 0:
                return JsonResponse({"error": "Invalid price"}, status=400)
        except InvalidOperation:
            return JsonResponse({"error": "Invalid price"}, status=400)

        item = get_object_or_404(
            MenuItem, id=item_id,
            tenant=request.user.tenant, outlet=request.user.outlet
        )
        item.price = price
        item.save(update_fields=["price"])
        return JsonResponse({"success": True})
    except Exception:
        logger.exception("Error updating item price")
        return JsonResponse({"error": "Could not update the price. Please try again."}, status=500)


@login_required
@tenant_required
@role_required("owner", "manager", "cashier")
@require_POST
def toggle_item(request, item_id):
    item = get_object_or_404(
        MenuItem, id=item_id,
        tenant=request.user.tenant, outlet=request.user.outlet
    )
    item.is_available = not item.is_available
    item.save(update_fields=["is_available"])
    return JsonResponse({"success": True})


@login_required
@tenant_required
@role_required("owner", "manager", "cashier")
@feature_required("platform_sync")
@require_POST
def toggle_platform_availability(request, item_id):
    try:
        data     = json.loads(request.body)
        platform = data.get("platform")
        item     = get_object_or_404(
            MenuItem, id=item_id,
            tenant=request.user.tenant, outlet=request.user.outlet
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
    except Exception:
        logger.exception("Error toggling platform availability")
        return JsonResponse({"error": "Could not update availability. Please try again."}, status=400)


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def update_station(request, item_id):
    try:
        data       = json.loads(request.body)
        station_id = data.get("station")
        item       = get_object_or_404(
            MenuItem, id=item_id,
            tenant=request.user.tenant, outlet=request.user.outlet
        )
        if station_id:
            station = KitchenStation.objects.get(
                id=station_id, tenant=request.user.tenant, outlet=request.user.outlet
            )
            item.station = station
            station_name = station.name
        else:
            item.station = None
            station_name = None
        item.save(update_fields=["station"])
        return JsonResponse({"success": True, "station_name": station_name})
    except Exception:
        logger.exception("Error updating item station")
        return JsonResponse({"error": "Could not update the station. Please try again."}, status=400)


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def add_recipe(request):
    try:
        data         = json.loads(request.body)
        item_id      = data.get("menu_item")
        inventory_id = data.get("inventory_item")
        quantity     = data.get("quantity")
        # None keeps an existing row's unit unchanged, or defaults to the
        # inventory item's own unit for a new row — see recipe_service.
        unit = data.get("unit")

        if not quantity:
            return JsonResponse({"error": "Quantity required"}, status=400)

        menu_item = get_object_or_404(
            MenuItem, id=item_id,
            tenant=request.user.tenant, outlet=request.user.outlet
        )
        inventory = get_object_or_404(
            InventoryItem, id=inventory_id,
            tenant=request.user.tenant, outlet=request.user.outlet
        )

        try:
            upsert_recipe(menu_item, inventory, quantity, unit)
        except RecipeUnitMismatchError as e:
            return JsonResponse({"error": str(e)}, status=400)

        return JsonResponse({"success": True})
    except Exception:
        logger.exception("Error adding recipe")
        return JsonResponse({"error": "Could not add the recipe. Please try again."}, status=500)
