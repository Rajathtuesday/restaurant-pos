"""Menu management page (owner/manager portal)."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render

from core.decorators import tenant_required
from menu.models import MenuCategory
from setup.models import KitchenStation
from inventory.models import InventoryItem


def menu_page_data(categories, kind):
    """Everything static/js/menu/management.js reads: each dish as the edit
    form holds it, and the addresses (0 stands in for an id)."""
    from django.urls import reverse
    dishes = {}
    for category in categories:
        for item in category.items.all():
            dishes[str(item.id)] = {
                "id": item.id, "name": item.name, "price": str(item.price), "description": item.description or "",
                "category": item.category_id, "station": item.station_id or "", "prepTime": item.estimated_prep_time,
                "isVeg": bool(item.is_veg), "parcelCharge": str(item.parcel_charge or 0),
            }
    return {
        "kind": kind,
        "dishes": dishes,
        "urls": {
            "createCategory": reverse("create_category"),
            "createItem": reverse("create_menu_item"),
            "updateItem": reverse("update_menu_item", args=[0]),
            "toggleItem": reverse("toggle_item", args=[0]),
            "updateStation": reverse("update_station", args=[0]),
            "addRecipe": reverse("add_recipe"),
            "sync": reverse("sync_menu_to_outlets"),
            "aiImport": reverse("ai_menu_importer"),
            "aiStatus": reverse("ai_import_status", args=["0"]),
            # a Rasova menu file given to the AI import goes to its check
            "menuFileCheck": reverse("menu_file_check"),
            "recipeStart": reverse("recipe-import-start"),
            "recipeStatus": reverse("recipe-import-status", args=[0]),
            "recipeReview": reverse("recipe-import-review", args=[0]),
        },
    }


@login_required
@tenant_required
def menu_management(request):
    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()

    categories = (
        MenuCategory.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet, is_active=True)
        .prefetch_related("items", "items__station", "items__recipes", "items__recipes__inventory_item")
    )
    inventory = InventoryItem.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet,
    )
    stations = KitchenStation.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet, is_active=True,
    )
    template = (
        "menu/qsr_menu_management.html"
        if request.user.tenant.tenant_type in ("franchise", "cafe")
        else "menu/menu_management.html"
    )
    return render(request, template, {
        "categories": categories, "inventory": inventory, "stations": stations,
        "page_data": menu_page_data(categories, "qsr" if template.startswith("menu/qsr") else "full"),
    })
