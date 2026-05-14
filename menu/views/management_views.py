"""Menu management page (owner/manager portal)."""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render

from core.decorators import tenant_required
from menu.models import MenuCategory
from setup.models import KitchenStation
from inventory.models import InventoryItem


@login_required
@tenant_required
def menu_management(request):
    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()

    categories = (
        MenuCategory.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet, is_active=True)
        .prefetch_related("items", "items__recipes", "items__recipes__inventory_item")
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
    })
