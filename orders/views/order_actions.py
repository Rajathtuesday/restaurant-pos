import json
import logging
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required

from core.decorators import tenant_required, role_required
from orders.models import Order, OrderItem
from orders.exceptions import OrderError

logger = logging.getLogger("pos.orders")


def _json_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _reason(data, default):
    reason = str(data.get("reason") or "").strip()
    return reason[:255] or default


def _made(data):
    # true / false from the edit sheet, or absent to use the suggestion.
    made = data.get("made")
    if made is None or isinstance(made, bool):
        return made
    raise OrderError("'made' must be true or false.")


@login_required
@tenant_required
@role_required("owner", "manager", "cashier", "captain")
@require_POST
def cancel_order(request, order_id):
    """
    Cancels a whole order. Body: {"reason": "...", "include_made": true}.

    If dishes on it were already made, the first call answers 409 with
    needs_confirm and the list, so the screen can ask "cancel them as
    wastage, or keep them and bill?". Repeating with include_made=true and a
    reason cancels them; served dishes need a manager. See
    void_service.cancel_whole_order.
    """
    from orders.services.void_service import cancel_whole_order, MadeDishesNeedConfirmation

    data = _json_body(request)
    try:
        cancel_whole_order(
            request.user, order_id,
            reason=str(data.get("reason") or ""),
            include_made=data.get("include_made") is True,
        )
        logger.info("User %s cancelled Order #%s", request.user.username, order_id)
        return JsonResponse({"success": True})

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except MadeDishesNeedConfirmation as e:
        return JsonResponse({
            "needs_confirm": True,
            "error": str(e),
            "needs_manager": (
                any(i.status == "served" for i in e.items)
                and getattr(request.user, "role", None) not in ("manager", "owner")
            ),
            "made_items": [
                {"name": i.menu_item.name if i.menu_item else "Unknown", "quantity": i.quantity, "status": i.status}
                for i in e.items
            ],
        }, status=409)
    except OrderError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        logger.error("Error cancelling order #%s: %s", order_id, e, exc_info=True)
        return JsonResponse({"error": "Server error"}, status=500)


@login_required
@tenant_required
@role_required("owner", "manager", "cashier", "captain")
@require_POST
def cancel_item(request, item_id):
    """
    Voids a specific order item. Delegates to void_service.void_order_item
    for inventory restoration (unit-converted, includes modifier-linked
    inventory) and table-state recalculation — this used to duplicate that
    logic inline without either of those, so a cancelled item after KOT-send
    never returned its deducted stock and never updated the table.
    """
    from orders.services.void_service import void_order_item

    try:
        data = _json_body(request)
        item = void_order_item(
            request.user, item_id, _reason(data, "Manual Item Cancellation"), made=_made(data),
        )
        # void_order_item recalculates totals on its own freshly-locked Order
        # object, not item.order (held before the call) — re-fetch, don't
        # trust the stale one.
        new_total = Order.objects.get(id=item.order_id).grand_total
        logger.info("User %s cancelled item #%s", request.user.username, item_id)
        return JsonResponse({"success": True, "new_total": float(new_total)})

    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except OrderError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        logger.error("Error cancelling item #%s: %s", item_id, e, exc_info=True)
        return JsonResponse({"error": "Server error"}, status=500)


@login_required
@tenant_required
@role_required("owner", "manager", "cashier", "captain")
@require_POST
def reduce_item(request, item_id):
    """
    Take some units off one order line. Body: {"reduce_by": 1, "reason": "..."}.
    Same roles as cancel_item; the service enforces the served-item manager
    rule and the tenant/outlet scope (another outlet's item is a 404).
    """
    from orders.services.void_service import reduce_item_quantity

    data = _json_body(request)
    try:
        item = reduce_item_quantity(
            request.user, item_id, data.get("reduce_by", 1),
            _reason(data, "Quantity reduced"), made=_made(data),
        )
        order = Order.objects.get(id=item.order_id)
        remaining = 0 if item.status == "voided" else item.quantity
        logger.info("User %s reduced item #%s to %s", request.user.username, item_id, remaining)
        return JsonResponse({
            "success": True,
            "remaining": remaining,
            "new_total": float(order.grand_total),
        })
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except OrderError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        logger.error("Error reducing item #%s: %s", item_id, e, exc_info=True)
        return JsonResponse({"error": "Server error"}, status=500)


@login_required
@tenant_required
@role_required("owner", "manager", "cashier", "captain")
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
        logger.error("toggle_parcel error for order %s: %s", order_id, e, exc_info=True)
        return JsonResponse({"error": "Server error"}, status=500)
