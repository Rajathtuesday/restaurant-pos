# menu/api.py

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.core.serializers.json import DjangoJSONEncoder

from core.decorators import tenant_required
from menu.models import MenuCategory, MenuItem

@login_required
@tenant_required
def api_categories(request):
    """
    Returns all active categories for the tenant/outlet.
    """
    categories = MenuCategory.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).order_by('order')

    data = [
        {
            "id": cat.id,
            "name": cat.name,
            "is_active": cat.is_active,
            "order": cat.order
        } for cat in categories
    ]
    
    return JsonResponse({"success": True, "data": data}, encoder=DjangoJSONEncoder)


@login_required
@tenant_required
def api_items(request):
    """
    Returns nested objects: Categories -> Items allowing offline POS hydration
    """
    categories = MenuCategory.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).prefetch_related('items').order_by('order')

    data = []
    for cat in categories:
        items_data = []
        for item in cat.items.all():
            # Filter internally for active items gracefully ignoring ghost items
            if not item.is_available:
                continue
                
            items_data.append({
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "price": item.price,
                "dietary_preference": item.dietary_preference,
                "spice_level": item.spice_level,
                "image_url": item.image.url if item.image else None,
            })
            
        data.append({
            "category_id": cat.id,
            "category_name": cat.name,
            "items": items_data
        })

    return JsonResponse({"success": True, "data": data}, encoder=DjangoJSONEncoder)
