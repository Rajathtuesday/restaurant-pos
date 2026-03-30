# orders/views/kitchen_views.py
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db import transaction

from core.decorators import tenant_required
from orders.models import Order, OrderItem, OrderEvent
from setup.models import KitchenStation

logger = logging.getLogger("pos.orders")


@login_required
@require_POST
@tenant_required
def send_to_kitchen(request, order_id):
    try:
        with transaction.atomic():
            order = (
                Order.objects
                .select_for_update()
                .get(
                    id=order_id,
                    tenant=request.user.tenant,
                    outlet=request.user.outlet
                )
            )

            if order.status != "open":
                return JsonResponse({"error": "Order is locked"}, status=400)

            from orders.services.kot_service import create_kot
            kots = create_kot(request.user, order)

            logger.info(f"User {request.user.username} sent order #{order.id} to kitchen")

            OrderEvent.objects.create(
                tenant=order.tenant,
                outlet=order.outlet,
                order=order,
                event_type="kot_sent",
                metadata={"kots": [k.kot_number for k in kots]},
                created_by=request.user
            )

            return JsonResponse({
                "success": True,
                "kots": [k.kot_number for k in kots]
            })

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
def kitchen_view(request):
    stations = KitchenStation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    )
    return render(request, "orders/kitchen.html", {"stations": stations})


@login_required
@tenant_required
def kitchen_data(request):
    from orders.services.kitchen_service import get_kitchen_data
    try:
        station_name = request.GET.get("station")
        data = get_kitchen_data(request.user, station_name)
        return JsonResponse({"kots": data})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
@tenant_required
def start_preparing(request, item_id):
    from orders.services.kitchen_service import set_item_preparing
    try:
        set_item_preparing(request.user, item_id)
        return JsonResponse({"success": True})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
@tenant_required
def mark_ready(request, item_id):
    from orders.services.kitchen_service import set_item_ready
    try:
        set_item_ready(request.user, item_id)
        return JsonResponse({"success": True})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@require_POST
@tenant_required
def serve_item(request, item_id):
    from orders.services.kitchen_service import set_item_served
    try:
        set_item_served(request.user, item_id)
        return JsonResponse({"success": True})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
