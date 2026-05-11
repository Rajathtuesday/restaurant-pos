import json
import hmac
import hashlib
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Prefetch, Q
from django.core.serializers.json import DjangoJSONEncoder
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from django.conf import settings

from core.decorators import tenant_required
from notifications.models import Notification
from orders.models import WaiterCall, KitchenMessage

@login_required
@tenant_required
def notification_api(request):
    """
    Unified endpoint for real-time notifications (Waiter Calls + Kitchen Messages).
    Used by the 8s global poller in base.html and standalone templates.
    """
    outlet = request.user.outlet
    tenant = request.user.tenant

    # 1. Active Waiter Calls (not resolved)
    waiter_calls = WaiterCall.objects.filter(
        tenant=tenant, outlet=outlet, is_resolved=False
    ).select_related('table').order_by('-created_at')

    # 2. Active Kitchen Messages (not acknowledged)
    kitchen_msgs = KitchenMessage.objects.filter(
        tenant=tenant, outlet=outlet, is_resolved=False
    ).select_related('order', 'order__table').order_by('-created_at')

    # 3. Unread System Notifications (the 'unread' specific path user mentioned)
    unread_system = Notification.objects.filter(
        tenant=tenant, outlet=outlet, is_read=False
    ).order_by('-created_at')

    return JsonResponse({
        "waiter_calls": {
            "count": waiter_calls.count(),
            "items": [{"id": c.id, "table": c.table.name} for c in waiter_calls]
        },
        "kitchen_messages": {
            "count": kitchen_msgs.count(),
            "items": [{
                "id": m.id, 
                "table": m.order.table.name if m.order.table else "Takeaway",
                "message": m.message
            } for m in kitchen_msgs]
        },
        "notifications": [
            {"id": n.id, "message": n.message} for n in unread_system
        ]
    })

from orders.models import Table, Order, OrderItem, Payment
from tenants.models import Tenant, Outlet
from setup.models import AggregatorConfig
from menu.models import MenuItem

logger = logging.getLogger("pos.api")

def is_ip_allowed(request):
    """
    Validates if the incoming request is from an allowed aggregator IP.
    HIGH-4: Implementation of IP allowlist.
    """
    if settings.DEBUG:
        return True
        
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
        
    # Placeholder for Zomato/Swiggy CIDR ranges
    # In production, these should be moved to settings.py
    ALLOWED_IPS = getattr(settings, 'AGGREGATOR_IP_ALLOWLIST', ['127.0.0.1'])
    
    # Simple check for now, can be expanded to CIDR range check
    return ip in ALLOWED_IPS

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



@csrf_exempt
@require_POST
def api_ingest_order(request):
    """
    Simulates a Webhook ingestion endpoint for Zomato / Swiggy / Online Orders.
    Validates HMAC signature and IP Allowlist (HIGH-4).
    """
    if not is_ip_allowed(request):
        logger.warning(f"Rejected ingest attempt from unauthorized IP: {request.META.get('REMOTE_ADDR')}")
        return JsonResponse({"error": "Unauthorized IP"}, status=403)

    try:
        data = json.loads(request.body)
        
        tenant_id = request.GET.get("tenant_id") or data.get("tenant_id")
        outlet_id = request.GET.get("outlet_id") or data.get("outlet_id")
        source = request.GET.get("source") or data.get("source", "web")
        aggregator_id = data.get("aggregator_order_id")
        items = data.get("items", [])
        
        tenant = Tenant.objects.get(id=tenant_id)
        outlet = Outlet.objects.get(id=outlet_id, tenant=tenant)
        config = AggregatorConfig.objects.get(tenant=tenant, outlet=outlet)
        
        # Verify HMAC Signature
        signature = request.headers.get("X-Signature")
        if not signature:
            return JsonResponse({"error": "Missing signature"}, status=401)
            
        secret = config.zomato_webhook_secret if source == "zomato" else config.swiggy_webhook_secret
        if not secret:
            return JsonResponse({"error": "Aggregator not configured"}, status=401)
            
        expected_sig = hmac.new(
            secret.encode(), 
            request.body, 
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected_sig, signature):
            return JsonResponse({"error": "Invalid signature"}, status=401)
        
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
                menu_item_id = i.get("menu_item_id")
                try:
                    menu_item = MenuItem.objects.get(id=menu_item_id, tenant=tenant, outlet=outlet)
                except MenuItem.DoesNotExist:
                    return JsonResponse(
                        {"error": f"Menu item id={menu_item_id} not found or not available at this outlet"},
                        status=422
                    )
                qty = i.get("quantity", 1)
                
                # Create item
                order_item = OrderItem.objects.create(
                    order=order,
                    menu_item=menu_item,
                    quantity=qty,
                    price=menu_item.price,
                    gst_percentage=menu_item.gst_percentage,
                    total_price=menu_item.price * qty,
                    status="sent" # Send to kitchen automatically
                )
                
            # Re-calculate
            order.recalculate_totals()

            # FIX: Create a Payment row so revenue appears in daily_sales reports.
            # Aggregator orders arrive pre-paid — the platform has already collected
            # the money. We record it as a single payment at the order's grand_total
            # using a method name that matches the aggregator source.
            payment_method = source if source in ("zomato", "swiggy", "uber_eats", "web") else "cash"
            Payment.objects.create(
                order=order,
                method=payment_method,
                amount=order.grand_total,
                reference=aggregator_id or "",
                created_by=None,
            )

            # Auto KOT Gen
            if config.auto_accept_orders:
                from orders.services.kitchen_service import send_order_to_kitchen
                send_order_to_kitchen(order, user=None)

            # Assign online token if tenant uses token_system
            from core.features import has_feature
            if has_feature(tenant, "token_system"):
                from orders.views.token_views import assign_online_token
                from core.utils import get_business_date
                business_date = get_business_date(timezone.now(), outlet)
                tok = assign_online_token(order, outlet, tenant, business_date)
                logger.info(
                    "Online token %s assigned to order %s (source=%s)",
                    tok.display_number, order.id, source,
                )

        return JsonResponse({"success": True, "order_id": order.id, "order_number": order.order_number})
        
    except Exception as e:
        logger.exception("Failed to ingest order via API")
        return JsonResponse({"error": "Internal Server Error"}, status=500)

