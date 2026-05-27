# orders/views/order_views.py
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from django.db import transaction
from core.decorators import tenant_required, feature_required
from orders.models import Order, TableMerge, OrderItem
from orders.services.event_service import log_event

logger = logging.getLogger("pos.orders")


@login_required
@tenant_required
@feature_required("running_order")
def running_order_view(request, order_id):
    order = Order.objects.filter(
        id=order_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).first()

    if not order:
        return JsonResponse({"error": "Order not found"}, status=404)

    return render(request, "orders/running_order.html", {"order": order})


@login_required
@tenant_required
@feature_required("running_order")
def running_order_items(request):
    try:
        table_id = request.GET.get("table")
        order_id = request.GET.get("order")

        tenant = request.user.tenant
        outlet = request.user.outlet
        
        order = None

        if order_id:
            try:
                order_id = int(order_id)
                order = (
                    Order.objects
                    .filter(id=order_id, tenant=tenant, outlet=outlet)
                    .prefetch_related("items__menu_item", "items__modifiers")
                    .first()
                )
            except (ValueError, TypeError):
                pass
        elif table_id:
            try:
                table_id = int(table_id)
                # Resolve table merge
                merge = (
                    TableMerge.objects
                    .filter(
                        tenant=tenant, outlet=outlet,
                        is_active=True, tables__id=table_id
                    )
                    .select_related("primary_table")
                    .first()
                )
                if merge:
                    table_id = merge.primary_table.id

                order = (
                    Order.objects
                    .filter(
                        tenant=tenant, outlet=outlet,
                        table_id=table_id,
                        status__in=["open", "billing"]
                    )
                    .prefetch_related("items__menu_item", "items__modifiers")
                    .order_by("-created_at")
                    .first()
                )
            except (ValueError, TypeError):
                pass

        if not order:
            return JsonResponse({"items": [], "order_id": None, "order_status": None})

        items = []
        for i in order.items.exclude(status="voided").order_by("id"):
            item_name = i.menu_item.name if i.menu_item else "Unknown Item"
            items.append({
                "id": i.id,
                "name": item_name,
                "quantity": i.quantity,
                "status": i.status,
                "modifiers": [m.name for m in i.modifiers.all()]
            })

        return JsonResponse({
            "items": items,
            "order_id": order.id,
            "order_status": order.status,
            "grand_total": float(order.grand_total or 0),
            "parcel_on": float(order.parcel_surcharge or 0) > 0,
            "parcel_amount": float(order.parcel_surcharge or 0),
        })

    except Exception as e:
        logger.error(f"running_order_items error: {str(e)}")
        return JsonResponse({"items": [], "order_id": None})


@login_required
@tenant_required
@feature_required("running_order")
def running_order_data(request, order_id):
    order = (
        Order.objects
        .select_related("table")
        .prefetch_related("items__menu_item", "items__modifiers")
        .filter(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        .first()
    )

    if not order:
        return JsonResponse({"error": "Order not found"}, status=404)

    items = []
    for i in order.items.all():
        items.append({
            "id": i.id,
            "name": i.menu_item.name,
            "quantity": i.quantity,
            "status": i.status,
            "modifiers": [m.name for m in i.modifiers.all()]
        })

    return JsonResponse({
        "subtotal": float(order.subtotal),
        "gst": float(order.gst_total),
        "total": float(order.grand_total),
        "items": items
    })


@login_required
@tenant_required
@feature_required("qr_menu")
@require_POST
def approve_items(request, order_id):
    """Waiters approve items added via QR code."""
    try:
        tenant = request.user.tenant
        outlet = request.user.outlet
        
        with transaction.atomic():
            items = OrderItem.objects.filter(
                order_id=order_id,
                order__tenant=tenant,
                order__outlet=outlet,
                status="review"
            ).select_for_update()
            
            if not items.exists():
                return JsonResponse({"error": "No items found for approval"}, status=404)
            
            count = items.count()
            items.update(status="pending")
            
            order = Order.objects.get(id=order_id)
            
            # Trigger KOT creation for the newly approved items
            try:
                from orders.services.kot_service import create_kot
                create_kot(request.user, order)
            except Exception as kot_err:
                logger.error(f"KOT Creation failed during approval: {kot_err}")
                # We don't fail the whole request, but we log it
            
            log_event(order, "status_changed", request.user, {"action": "items_approved", "count": count})
            
            logger.info(f"User {request.user.username} approved {count} items for order #{order_id}")
            
        return JsonResponse({"success": True, "count": count})
    except Exception as e:
        logger.error(f"approve_items error: {str(e)}")
        return JsonResponse({"error": str(e)}, status=400)
