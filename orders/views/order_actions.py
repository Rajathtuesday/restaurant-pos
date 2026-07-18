import json
import logging
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from core.decorators import tenant_required, role_required
from orders.models import Order, OrderItem, OrderEvent
from orders.exceptions import OrderError

logger = logging.getLogger("pos.orders")

@login_required
@tenant_required
@role_required("owner", "manager", "cashier", "captain")
@require_POST
def cancel_order(request, order_id):
    """
    Cancels an entire order entirely. Voids all non-served items via
    void_service.void_order_item (restores inventory — this used to void
    items inline with zero inventory restoration), updates order status to
    cancelled, and frees up the table.

    Already-served items are deliberately left un-voided, matching prior
    behavior — this can strand a served-but-unbilled item once the order is
    marked cancelled (cancelled orders are excluded from every active-order
    query), a known, separate issue not fixed by this pass.
    """
    from orders.services.void_service import void_order_item

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

            for item in list(order.items.exclude(status__in=["voided", "served"])):
                try:
                    void_order_item(request.user, item.id, "Order Cancelled")
                except OrderError:
                    # Voided/served by a concurrent action since the list
                    # above was built — skip rather than fail the whole cancel.
                    continue

            order.refresh_from_db()
            order.status = "cancelled"
            order.closed_at = timezone.now()
            order.save(update_fields=["status", "closed_at"])
            order.recalculate_totals()

            # Cancelled orders are excluded from every active-order query, so
            # this table can never be billed through this order again
            # regardless of what update_table_state would compute — free it
            # unconditionally rather than risk it reading "ready" and
            # misleading staff into thinking stranded served items are
            # still billable.
            if order.table:
                order.table.state = "free"
                order.table.save(update_fields=["state"])

            OrderEvent.objects.create(
                tenant=tenant, outlet=outlet, order=order,
                event_type="order_cancelled",
                metadata={"reason": "Manual cancellation"},
                created_by=request.user
            )

        logger.info("User %s cancelled Order #%s", request.user.username, order_id)
        return JsonResponse({"success": True})

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
        item = void_order_item(request.user, item_id, "Manual Item Cancellation")
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
