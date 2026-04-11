# orders/views/order_views.py
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render

from core.decorators import tenant_required
from orders.models import Order, TableMerge

logger = logging.getLogger("pos.orders")


@login_required
@tenant_required
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
def running_order_items(request):
    try:
        table_id = request.GET.get("table")

        if not table_id:
            return JsonResponse({"items": [], "order_id": None})

        try:
            table_id = int(table_id)
        except (ValueError, TypeError):
            return JsonResponse({"items": [], "order_id": None})

        tenant = request.user.tenant
        outlet = request.user.outlet

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
            .prefetch_related("items__menu_item")
            .order_by("-created_at")
            .first()
        )

        if not order:
            return JsonResponse({"items": [], "order_id": None})

        items = []
        for i in order.items.exclude(status="served").order_by("id"):
            item_name = i.menu_item.name if i.menu_item else "Unknown Item"
            items.append({
                "id": i.id,
                "name": item_name,
                "quantity": i.quantity,
                "status": i.status
            })

        return JsonResponse({"items": items, "order_id": order.id})

    except Exception as e:
        logger.error(f"running_order_items error: {str(e)}")
        return JsonResponse({"items": [], "order_id": None})


@login_required
@tenant_required
def running_order_data(request, order_id):
    order = (
        Order.objects
        .select_related("table")
        .prefetch_related("items__menu_item")
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
            "status": i.status
        })

    return JsonResponse({
        "subtotal": float(order.subtotal),
        "gst": float(order.gst_total),
        "total": float(order.grand_total),
        "items": items
    })
