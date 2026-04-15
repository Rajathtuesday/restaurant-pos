# inventory/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import InventoryItem
from django.views.decorators.http import require_POST
from django.http import JsonResponse
import json
from decimal import Decimal
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
import logging

logger = logging.getLogger("pos.inventory")


@login_required
def inventory_board(request):

    if request.user.role not in ["owner","manager"]:
        return HttpResponseForbidden("Access denied")

    items = InventoryItem.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    return render(
        request,
        "inventory/inventory_board.html",
        {"items": items}
    )
    
    




@login_required
@require_POST
def restock_item(request, item_id):

    if request.user.role not in ["owner","manager"]:
        return HttpResponseForbidden()

    data = json.loads(request.body)

    quantity = Decimal(data.get("quantity","0"))

    item = InventoryItem.objects.get(
        id=item_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    item.add_stock(quantity)
    
    logger.info(f"User {request.user.username} restocked '{item.name}' with {quantity} {item.unit}. New stock: {item.stock}")

    return JsonResponse({
        "success":True,
        "new_stock":float(item.stock)
    })
    
    

@login_required
@require_POST
def create_inventory_item(request):

    if request.user.role not in ["owner","manager"]:
        return HttpResponseForbidden()

    data = json.loads(request.body)

    name = data.get("name")
    unit = data.get("unit")
    stock = Decimal(data.get("stock", "0"))
    threshold = Decimal(data.get("threshold", "0"))
    cost_price = Decimal(data.get("cost_price", "0.00"))

    if not name:
        return JsonResponse({"error": "Name required"}, status=400)

    item = InventoryItem.objects.create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        name=name,
        unit=unit,
        stock=stock,
        low_stock_threshold=threshold,
        cost_price=cost_price
    )
    
    logger.info(f"User {request.user.username} created new inventory item '{name}'")

    return JsonResponse({
        "success": True,
        "id": item.id
    })

@login_required
def purchase_order_view(request):
    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()
        
    items = InventoryItem.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )
    
    # Filter low stock locally just to calculate needed amounts safely
    low_stock_items = []
    for item in items:
        if item.is_low_stock:
            qty_needed = item.low_stock_threshold - item.stock + Decimal('10') # buffer
            low_stock_items.append({
                'item': item,
                'qty_needed': qty_needed
            })
            
    return render(request, "inventory/purchase_order.html", {
        "low_stock_items": low_stock_items,
        "date": timezone.now()
    })