# orders/views/kitchen_views.py
import logging
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db import transaction

from core.decorators import tenant_required, feature_required, role_required
from orders.models import Order, OrderItem, OrderEvent, KitchenMessage
from setup.models import KitchenStation

logger = logging.getLogger("pos.orders")


@login_required
@require_POST
@tenant_required
@role_required("owner", "manager", "cashier", "captain", "waiter")
@feature_required("kot_system")
def send_to_kitchen(request, order_id):
    """
    Generates a Kitchen Order Ticket (KOT) for an active order.
    Locks the order, creates the KOTBatch, and dispatches it to the Kitchen Display System (KDS).
    Records an OrderEvent for auditing.
    """
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

            if order.status not in ["open", "billing"]:
                return JsonResponse({"error": f"Order is {order.status} and cannot be sent to kitchen"}, status=400)

            from orders.services.kot_service import create_kot
            kots = create_kot(request.user, order)

            logger.info("User %s sent order #%s to kitchen", request.user.username, order.id)

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
@role_required("owner", "manager", "chef", "kitchen")
@feature_required("kitchen_display")
def kitchen_view(request):
    """
    Renders the Kitchen Display System (KDS) UI.
    Provides station filtering for multi-station kitchens.
    """
    stations = KitchenStation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    )
    return render(request, "orders/kitchen.html", {"stations": stations})


@login_required
@tenant_required
@role_required("owner", "manager", "chef", "kitchen")
@feature_required("kitchen_display")
def kitchen_data(request):
    """
    AJAX Polling endpoint for the Kitchen Display System (KDS).
    Returns real-time structured JSON of all active KOTs matching the requested station.
    """
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
@role_required("owner", "manager", "chef", "kitchen")
@feature_required("kitchen_display")
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
@role_required("owner", "manager", "chef", "kitchen")
@feature_required("kitchen_display")
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
@role_required("owner", "manager", "chef", "kitchen", "captain", "waiter", "cashier")
@feature_required("kitchen_display")
def serve_item(request, item_id):
    """
    Transitions an OrderItem status from 'ready' to 'served'.
    Triggered by waiters when they deliver the food to the customer's table.
    """
    from orders.services.kitchen_service import set_item_served
    try:
        set_item_served(request.user, item_id)
        return JsonResponse({"success": True})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)

@login_required
@require_POST
@tenant_required
@role_required("owner", "manager", "chef", "kitchen")
@feature_required("kitchen_display")
def bump_kot(request, kot_id):
    """Mark all pending/preparing items in a KOT as ready in one tap."""
    from orders.models import KOTBatch
    from orders.services.kitchen_service import set_item_ready
    try:
        # Scope by outlet as well as tenant — without order__outlet a multi-outlet
        # tenant's kitchen staff at outlet A could bump outlet B's KOT by id.
        kot = KOTBatch.objects.select_related("order").get(
            id=kot_id,
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet,
        )
        bumped = 0
        for item in kot.items.filter(status__in=["sent", "preparing"]):
            try:
                set_item_ready(request.user, item.id)
                bumped += 1
            except Exception as e:
                # Partial success is by design (one bad item shouldn't block
                # the rest), but a failure here used to be completely
                # invisible — kitchen staff just saw a lower bumped count
                # with no way to tell why.
                logger.warning("bump_kot: could not mark item %s ready: %s", item.id, e)
        logger.info("KOT #%s bumped - %d items marked ready by %s", kot.kot_number, bumped, request.user.username)
        return JsonResponse({"success": True, "bumped": bumped})
    except KOTBatch.DoesNotExist:
        return JsonResponse({"error": "KOT not found"}, status=404)


@login_required
@require_POST
@tenant_required
@role_required("owner", "manager", "chef", "kitchen", "captain", "waiter", "cashier")
@feature_required("kitchen_display")
def send_kitchen_message(request, order_id):
    """
    Allows waiters/staff to send urgent ad-hoc text messages to the kitchen KDS
    attached to a specific order (e.g., "Customer allergic to peanuts").
    """
    try:
        data = json.loads(request.body)
        message_text = data.get("message")
        if not message_text:
            return JsonResponse({"error": "Message text is required"}, status=400)
            
        order = Order.objects.get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
        
        KitchenMessage.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            order=order,
            message=message_text
        )
        return JsonResponse({"success": True})
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
