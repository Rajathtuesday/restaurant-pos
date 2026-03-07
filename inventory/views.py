# inventory/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import HttpResponseForbidden

from .models import InventoryItem


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
    
    
