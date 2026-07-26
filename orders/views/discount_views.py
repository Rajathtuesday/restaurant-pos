# orders/views/discount_views.py
import json
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, role_required
from orders.models import Order, OrderEvent, OrderItem
from orders.utils.order_utils import validate_order_editable

logger = logging.getLogger("pos.orders")


# -------------------------------------------------
# DISCOUNT
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "cashier", "captain", "owner")
def apply_discount(request, order_id):

    try:
        data = json.loads(request.body)
        discount_type = data.get("type", "percentage")
        value = Decimal(str(data.get("value", 0)))
        promo_id = data.get("promo_id")

        if discount_type not in ["percentage", "amount"]:
            return JsonResponse({"error": "Invalid discount type"}, status=400)

        if value < 0:
            return JsonResponse({"error": "Invalid discount value"}, status=400)

        if discount_type == "percentage" and value > 100:
            return JsonResponse({"error": "Percentage cannot exceed 100"}, status=400)

        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
            )
            if order.status in ["paid", "closed", "cancelled"]:
                return JsonResponse({"error": "Order is already paid, closed, or cancelled."}, status=400)

            if promo_id:
                from promos.models import Promo
                try:
                    promo = Promo.objects.get(id=promo_id, tenant=request.user.tenant)
                    ok, err = promo.validate_and_use(order.outlet, order.subtotal)
                    if not ok:
                        return JsonResponse({"error": err}, status=400)

                    # Apply values from the promo
                    discount_type = promo.discount_type
                    value = promo.discount_value
                except Promo.DoesNotExist:
                    return JsonResponse({"error": "Promo code not found"}, status=404)

            order.discount_type = discount_type
            order.discount_value = value
            order.save(update_fields=["discount_type", "discount_value"])
            order.recalculate_totals()

            logger.warning(
                "User %s applied %s discount of %s to order #%s",
                request.user.username, discount_type, value, order_id,
            )

            OrderEvent.objects.create(
                tenant=order.tenant, outlet=order.outlet, order=order,
                event_type="discount_applied",
                metadata={"action": "discount_applied", "type": discount_type, "value": str(value)},
                created_by=request.user
            )

        return JsonResponse({
            "success": True,
            "subtotal": float(order.subtotal),
            "gst": float(order.gst_total),
            "discount": float(order.discount_total),
            "total": float(order.grand_total)
        })

    except Exception as e:
        logger.exception("Error applying discount for order #%s", order_id)
        return JsonResponse({"error": "Discount could not be applied. Please try again."}, status=500)


# -------------------------------------------------
# COMPLIMENTARY ITEM
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "captain", "owner")
def make_item_complimentary(request, item_id):

    try:
        # Lock the item's order row for the read-modify-recalculate, matching
        # apply_item_discount. Without the lock a concurrent discount/void/
        # payment on the same order can race with this write and lose an update.
        with transaction.atomic():
            item = (
                OrderItem.objects.select_for_update().select_related("order")
                .get(id=item_id, order__tenant=request.user.tenant, order__outlet=request.user.outlet)
            )
            validate_order_editable(item.order)
            item.is_complimentary = True
            item.save(update_fields=["is_complimentary"])
            item.order.recalculate_totals()
            OrderEvent.objects.create(
                tenant=item.order.tenant, outlet=item.order.outlet, order=item.order,
                event_type="item_complimentary",
                metadata={"item_id": item.id},
                created_by=request.user
            )
        logger.warning("User %s marked item #%s as complimentary", request.user.username, item_id)
        return JsonResponse({"success": True})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)


# -------------------------------------------------
# PER-ITEM DISCOUNT  (Manager/Owner only)
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "cashier", "captain", "owner")
def apply_item_discount(request, item_id):
    try:
        data = json.loads(request.body)
        discount_pct = Decimal(str(data.get("percent", 0)))

        if discount_pct < 0 or discount_pct > 100:
            return JsonResponse({"error": "Invalid percentage"}, status=400)

        with transaction.atomic():
            item = (
                OrderItem.objects.select_related("order")
                .select_for_update()
                .get(id=item_id, order__tenant=request.user.tenant, order__outlet=request.user.outlet)
            )
            validate_order_editable(item.order)

            item.item_discount_pct = discount_pct
            item.save(update_fields=["item_discount_pct"])
            item.order.recalculate_totals()

            OrderEvent.objects.create(
                tenant=item.order.tenant, outlet=item.order.outlet, order=item.order,
                event_type="item_discount_applied",
                metadata={"action": "item_discount_applied", "item_id": item.id, "discount_pct": str(discount_pct)},
                created_by=request.user
            )

        logger.warning("User %s applied %s%% discount to item #%s", request.user.username, discount_pct, item_id)
        return JsonResponse({"success": True, "new_total": float(item.order.grand_total)})

    except Exception:
        logger.exception("Error applying item discount to item #%s", item_id)
        return JsonResponse({"error": "Discount could not be applied. Please try again."}, status=500)


# -------------------------------------------------
# LOG PAYMENT BYPASS
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
def log_bypass(request, order_id):
    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(
                id=order_id,
                tenant=request.user.tenant,
                outlet=request.user.outlet
            )

            if order.status in ["paid", "closed"]:
                return JsonResponse({"error": "Order already completed"}, status=400)

            # Enforce daily bypass limit for non-owners
            if request.user.role != "owner":
                import pytz
                ist = pytz.timezone('Asia/Kolkata')
                now_ist = timezone.now().astimezone(ist)
                # Keep the datetime timezone-aware; stripping tzinfo causes Django ORM
                # to compare a naive dt against a tz-aware field, giving wrong results.
                today_ist_start = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)

                bypass_count = OrderEvent.objects.filter(
                    tenant=request.user.tenant,
                    outlet=request.user.outlet,
                    created_by=request.user,
                    event_type="status_changed",
                    created_at__gte=today_ist_start
                ).filter(metadata__action="payment_gate_bypassed").count()

                if bypass_count >= 3:
                    return JsonResponse({"error": "Daily payment bypass limit (3) reached. Contact owner."}, status=403)

            logger.warning(
                "User %s bypassed payment gate for order #%s",
                request.user.username, order_id,
            )

            # Actually close the order
            order.status = "closed"
            order.closed_at = timezone.now()
            order.save(update_fields=["status", "closed_at"])

            if order.table:
                order.table.state = "cleaning"
                order.table.save(update_fields=["state"])

            OrderEvent.objects.create(
                tenant=order.tenant, outlet=order.outlet, order=order,
                event_type="status_changed",
                metadata={
                    "action": "payment_gate_bypassed",
                    "role": request.user.role,
                    "bypassed_by": request.user.username
                },
                created_by=request.user
            )

        return JsonResponse({"success": True, "message": "Order closed via bypass"})

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception:
        logger.exception("Bypass error for order #%s", order_id)
        return JsonResponse({"error": "Payment bypass could not be processed. Please try again."}, status=500)
