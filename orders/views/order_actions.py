import json
import logging
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from core.decorators import tenant_required
from orders.models import Order, OrderItem, OrderEvent

logger = logging.getLogger("pos.orders")

@login_required
@tenant_required
@require_POST
def cancel_order(request, order_id):
    """
    Cancels an entire order entirely. Voids all non-served items,
    updates order status to cancelled, and frees up the table.
    """
    try:
        tenant = request.user.tenant
        outlet = request.user.outlet
        
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(
                id=order_id, tenant=tenant, outlet=outlet
            ).first()
            
            if not order:
                return JsonResponse({"error": "Order not found"}, status=404)
                
            if order.status in ["paid", "closed", "cancelled"]:
                return JsonResponse({"error": f"Cannot cancel order in {order.status} state"}, status=400)
                
            # Iterate through items and void them
            items_to_update = []
            for item in order.items.all():
                if item.status not in ["voided", "served"]:
                    item.status = "voided"
                    item.void_reason = "Order Cancelled"
                    item.voided_by = request.user
                    item.voided_at = timezone.now()
                    items_to_update.append(item)
            
            if items_to_update:
                OrderItem.objects.bulk_update(items_to_update, ["status", "void_reason", "voided_by", "voided_at"])
            
            # Update order status
            order.status = "cancelled"
            order.closed_at = timezone.now()
            order.save(update_fields=["status", "closed_at"])
            
            # Recalculate totals (should become zero if all voided)
            order.recalculate_totals()
            
            # Free up the table
            if order.table:
                order.table.state = "free"
                order.table.save(update_fields=["state"])
                
            # Log event
            OrderEvent.objects.create(
                tenant=tenant, outlet=outlet, order=order,
                event_type="order_cancelled",
                metadata={"reason": "Manual cancellation"},
                created_by=request.user
            )
            
        logger.info(f"User {request.user.username} cancelled Order #{order_id}")
        return JsonResponse({"success": True})
        
    except Exception as e:
        logger.error(f"Error cancelling order #{order_id}: {str(e)}")
        return JsonResponse({"error": "Server error", "detail": str(e)}, status=500)


@login_required
@tenant_required
@require_POST
def cancel_item(request, item_id):
    """
    Voids a specific order item.
    """
    try:
        tenant = request.user.tenant
        outlet = request.user.outlet
        
        with transaction.atomic():
            item = OrderItem.objects.select_related("order").select_for_update().filter(
                id=item_id, order__tenant=tenant, order__outlet=outlet
            ).first()
            
            if not item:
                return JsonResponse({"error": "Item not found"}, status=404)
                
            if item.status in ["voided", "served"]:
                return JsonResponse({"error": f"Cannot cancel item in {item.status} state"}, status=400)
                
            item.status = "voided"
            item.void_reason = "Manual Item Cancellation"
            item.voided_by = request.user
            item.voided_at = timezone.now()
            item.save(update_fields=["status", "void_reason", "voided_by", "voided_at"])
            
            order = item.order
            order.recalculate_totals()
            
            # Log event
            OrderEvent.objects.create(
                tenant=tenant, outlet=outlet, order=order,
                event_type="item_voided",
                metadata={"item_id": item.id, "item_name": item.menu_item.name if item.menu_item else "Unknown"},
                created_by=request.user
            )
            
        return JsonResponse({"success": True, "new_total": float(order.grand_total)})
        
    except Exception as e:
        logger.error(f"Error cancelling item #{item_id}: {str(e)}")
        return JsonResponse({"error": "Server error", "detail": str(e)}, status=500)


@login_required
@tenant_required
@require_POST
def toggle_parcel(request, order_id):
    """
    Toggle parcel surcharge on/off for an order.
    Uses the outlet's configured parcel_charge_amount.
    Returns updated grand_total and parcel_surcharge so the UI can update instantly.
    """
    from decimal import Decimal
    try:
        order = Order.objects.get(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            status__in=["open", "billing"],
        )
        charge   = Decimal(str(getattr(order.outlet, "parcel_charge_amount", "5") or "0"))
        per_item = getattr(order.outlet, "parcel_charge_per_item", True)

        # Toggle: if already set → remove; if zero → add
        if order.parcel_surcharge > 0:
            order.parcel_surcharge = Decimal("0")
        else:
            active_items = list(order.items.exclude(status="voided").select_related("menu_item"))
            # Per-item mode: if ANY menu item has its own parcel_charge set, sum those
            per_item_total = sum(
                (item.menu_item.parcel_charge or Decimal("0")) * item.quantity
                for item in active_items
                if item.menu_item and (item.menu_item.parcel_charge or Decimal("0")) > 0
            )
            if per_item_total > 0:
                order.parcel_surcharge = per_item_total
            elif per_item:
                total_qty = sum(item.quantity for item in active_items)
                order.parcel_surcharge = charge * Decimal(total_qty)
            else:
                order.parcel_surcharge = charge

        order.save(update_fields=["parcel_surcharge"])
        order.recalculate_totals()

        return JsonResponse({
            "success": True,
            "parcel_on": order.parcel_surcharge > 0,
            "parcel_amount": float(order.parcel_surcharge),
            "grand_total": float(order.grand_total),
        })
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found or already closed"}, status=404)
    except Exception as e:
        logger.error("toggle_parcel error for order %s: %s", order_id, e)
        return JsonResponse({"error": str(e)}, status=500)
