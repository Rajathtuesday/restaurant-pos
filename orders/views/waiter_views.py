# orders/views/waiter_views.py
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required
from orders.models import WaiterCall
from kitchen.models import KitchenMessage

logger = logging.getLogger("pos.orders")


@login_required
@tenant_required
@feature_required("waiter_call")
def waiter_dashboard(request):
    calls = WaiterCall.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_resolved=False
    ).select_related("table").order_by("-created_at")

    kitchen_messages = KitchenMessage.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_resolved=False
    ).select_related("order", "order__table").order_by("-created_at")
    if request.user.role == 'waiter':
        kitchen_messages = kitchen_messages.filter(order__created_by=request.user)

    return render(request, "orders/waiter_dashboard.html", {
        "calls": calls,
        "kitchen_messages": kitchen_messages
    })


@login_required
@tenant_required
@feature_required("waiter_call")
@require_POST
def resolve_waiter_call(request, call_id):
    try:
        call = WaiterCall.objects.get(
            id=call_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        call.is_resolved = True
        call.save(update_fields=["is_resolved"])

        logger.info("User %s resolved waiter call #%s for table %s", request.user.username, call_id, call.table.name)
        return JsonResponse({"success": True})

    except WaiterCall.DoesNotExist:
        return JsonResponse({"error": "Call not found"}, status=404)


@login_required
@tenant_required
@feature_required("kitchen_display")
@require_POST
def resolve_kitchen_message(request, message_id):
    try:
        msg = KitchenMessage.objects.get(
            id=message_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        msg.is_resolved = True
        msg.save(update_fields=["is_resolved"])

        logger.info("User %s read kitchen message #%s", request.user.username, message_id)
        return JsonResponse({"success": True})

    except KitchenMessage.DoesNotExist:
        return JsonResponse({"error": "Message not found"}, status=404)
