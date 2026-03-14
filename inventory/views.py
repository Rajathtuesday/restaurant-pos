# inventory/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import InventoryItem
from django.views.decorators.http import require_POST
from django.http import JsonResponse
import json
from decimal import Decimal
from django.http import JsonResponse, HttpResponseForbidden


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

    return JsonResponse({
        "success":True,
        "new_stock":float(item.stock)
    })