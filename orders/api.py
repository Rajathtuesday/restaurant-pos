# orders/api.py

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Prefetch
from django.core.serializers.json import DjangoJSONEncoder

from core.decorators import tenant_required
from orders.models import Table, Order, OrderItem

@login_required
@tenant_required
def api_tables(request):
    """
    Real-time tables state checking.
    """
    tables = Table.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).order_by('name')

    data = []
    for table in tables:
        data.append({
            "id": table.id,
            "name": table.name,
            "state": table.state,
            "is_active": table.is_active
        })

    return JsonResponse({"success": True, "data": data}, encoder=DjangoJSONEncoder)


@login_required
@tenant_required
def api_active_orders(request):
    """
    Returns full order/ticket data for the active outlet avoiding race-condition deadlocks.
    """
    orders = Order.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        status__in=["open", "billing"]
    ).prefetch_related(
        Prefetch("items", queryset=OrderItem.objects.select_related("menu_item"))
    ).select_related("table")

    data = []
    for order in orders:
        items_data = []
        for item in order.items.all():
            items_data.append({
                "id": item.id,
                "name": item.menu_item.name if item.menu_item else "Unknown (Deleted)",
                "quantity": item.quantity,
                "status": item.status,
                "price": item.price,
                "total_price": item.total_price,
                "is_complimentary": item.is_complimentary,
                "void_reason": item.void_reason
            })
            
        data.append({
            "id": order.id,
            "order_number": order.order_number,
            "table_id": order.table_id if order.table else None,
            "table_name": order.table.name if order.table else "Walk-in",
            "status": order.status,
            "subtotal": order.subtotal,
            "gst_total": order.gst_total,
            "discount_total": order.discount_total,
            "grand_total": order.grand_total,
            "created_at": str(order.created_at),
            "items": items_data
        })

    return JsonResponse({"success": True, "data": data}, encoder=DjangoJSONEncoder)

from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
import json

@csrf_exempt
@require_POST
def api_ingest_order(request):
    """
    Simulates a Webhook ingestion endpoint for Zomato / Swiggy / Online Orders.
    Requires Tenant & Outlet mapping via API keys in production, but here we expect 'tenant_id' and 'outlet_id' in JSON for demonstration.
    """
    try:
        data = json.loads(request.body)
        
        tenant_id = data.get("tenant_id")
        outlet_id = data.get("outlet_id")
        source = data.get("source", "web")
        aggregator_id = data.get("aggregator_order_id")
        items = data.get("items", [])
        
        from tenants.models import Tenant, Outlet
        tenant = Tenant.objects.get(id=tenant_id)
        outlet = Outlet.objects.get(id=outlet_id, tenant=tenant)
        
        from django.db import transaction
        from menu.models import MenuItem

        with transaction.atomic():
            # Check if order already ingested
            if aggregator_id and Order.objects.filter(tenant=tenant, outlet=outlet, aggregator_order_id=aggregator_id).exists():
                return JsonResponse({"error": "Order already exists"}, status=400)
                
            # Create Order
            order = Order.objects.create(
                tenant=tenant,
                outlet=outlet,
                source=source,
                aggregator_order_id=aggregator_id,
                status="paid",  # Aggregator orders usually come pre-paid
            )
            
            # Add Items
            for i in items:
                menu_item = MenuItem.objects.get(id=i.get("menu_item_id"), tenant=tenant, outlet=outlet)
                qty = i.get("quantity", 1)
                
                # Create item
                order_item = OrderItem.objects.create(
                    order=order,
                    menu_item=menu_item,
                    quantity=qty,
                    price=menu_item.price,
                    gst_percentage=0, # Simplified for example
                    total_price=menu_item.price * qty,
                    status="sent" # Send to kitchen automatically
                )
                
            # Re-calculate
            order.recalculate_totals()
            
            # Auto KOT Gen
            from orders.services.kitchen_service import send_order_to_kitchen
            send_order_to_kitchen(order, user=None)
            
        return JsonResponse({"success": True, "order_id": order.id, "order_number": order.order_number})
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=400)

