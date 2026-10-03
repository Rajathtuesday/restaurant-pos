# orders/views/discount_views.py
import json
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, role_required
from orders.models import Order, OrderEvent, OrderItem
from orders.utils.order_utils import validate_order_editable
from orders.services.payment_service import mark_ready_items_served
from orders.services.discount_policy import (
    DiscountNeedsManager, DiscountRefused, bill_discount_percent, check_within_limit, read_reason,
)
from orders.services.row_locks import lock_order_of_item
from promos.services import attach_promo, release_promo

logger = logging.getLogger("pos.orders")


# -------------------------------------------------
# DISCOUNT
# -------------------------------------------------
# Who may give how much, and the reason it needs: orders/services/discount_policy.py.
# A promo's use is taken and given back by promos/services.py.

def _refused(e):
    """A discount the policy refused, as the response staff see."""
    return JsonResponse({"error": str(e)}, status=403 if isinstance(e, DiscountNeedsManager) else 400)


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
                except Promo.DoesNotExist:
                    return JsonResponse({"error": "Promo code not found"}, status=404)
                ok, err = attach_promo(order, promo)
                if not ok:
                    return JsonResponse({"error": err}, status=400)
                # The promo's own values, never what the screen sent.
                discount_type = promo.discount_type
                value = promo.discount_value
                details = {"via": "promo", "promo_id": promo.id, "promo_name": promo.name,
                           "promo_code": promo.code}
            else:
                if value > 0:
                    reason = read_reason(data.get("reason"))
                    check_within_limit(
                        request.user, order.outlet,
                        bill_discount_percent(discount_type, value, order.subtotal),
                    )
                    details = {"via": "manual", "reason": reason}
                else:
                    details = {"via": "removed"}
                # A typed discount, or taking the discount off, replaces any
                # promo the bill had, and gives that promo's use back.
                release_promo(order)

            order.discount_type = discount_type
            order.discount_value = value
            order.save(update_fields=["discount_type", "discount_value", "promo", "promo_name"])
            order.recalculate_totals()

            logger.warning(
                "User %s applied %s discount of %s to order #%s",
                request.user.username, discount_type, value, order_id,
            )

            OrderEvent.objects.create(
                tenant=order.tenant, outlet=order.outlet, order=order,
                event_type="discount_applied",
                metadata={"action": "discount_applied", "type": discount_type, "value": str(value), **details},
                created_by=request.user
            )

        return JsonResponse({
            "success": True,
            "subtotal": float(order.subtotal),
            "gst": float(order.gst_total),
            "discount": float(order.discount_total),
            "total": float(order.grand_total)
        })

    except DiscountRefused as e:
        return _refused(e)
    except Exception:
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
        try:
            data = json.loads(request.body or b"{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        # Lock the item's order row for the read-modify-recalculate, matching
        # apply_item_discount. Without the lock a concurrent discount/void/
        # payment on the same order can race with this write and lose an update.
        with transaction.atomic():
            lock_order_of_item(request.user, item_id)  # order before line, see row_locks
            item = (
                OrderItem.objects.select_for_update().select_related("order", "order__outlet")
                .get(id=item_id, order__tenant=request.user.tenant, order__outlet=request.user.outlet)
            )
            validate_order_editable(item.order)
            # A free dish is a 100% discount on it: the same reason and the
            # same limit as any other discount.
            reason = read_reason(data.get("reason"))
            check_within_limit(request.user, item.order.outlet, Decimal("100"), what="A free dish")
            item.is_complimentary = True
            item.save(update_fields=["is_complimentary"])
            item.order.recalculate_totals()
            OrderEvent.objects.create(
                tenant=item.order.tenant, outlet=item.order.outlet, order=item.order,
                event_type="item_complimentary",
                metadata={"item_id": item.id, "reason": reason},
                created_by=request.user
            )
        logger.warning("User %s marked item #%s as complimentary", request.user.username, item_id)
        return JsonResponse({"success": True})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except DiscountRefused as e:
        return _refused(e)
    except ValidationError as e:
        # validate_order_editable: the bill is being paid or already closed
        return JsonResponse({"error": " ".join(e.messages)}, status=400)


# -------------------------------------------------
# PER-ITEM DISCOUNT
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
            lock_order_of_item(request.user, item_id)  # order before line, see row_locks
            item = (
                OrderItem.objects.select_related("order", "order__outlet")
                .select_for_update()
                .get(id=item_id, order__tenant=request.user.tenant, order__outlet=request.user.outlet)
            )
            validate_order_editable(item.order)

            details = {}
            if discount_pct > 0:
                details["reason"] = read_reason(data.get("reason"))
                check_within_limit(request.user, item.order.outlet, discount_pct, what="A dish discount")

            item.item_discount_pct = discount_pct
            item.save(update_fields=["item_discount_pct"])
            item.order.recalculate_totals()

            OrderEvent.objects.create(
                tenant=item.order.tenant, outlet=item.order.outlet, order=item.order,
                event_type="item_discount_applied",
                metadata={"action": "item_discount_applied", "item_id": item.id,
                          "discount_pct": str(discount_pct), **details},
                created_by=request.user
            )

        logger.warning("User %s applied %s%% discount to item #%s", request.user.username, discount_pct, item_id)
        return JsonResponse({"success": True, "new_total": float(item.order.grand_total)})

    except DiscountRefused as e:
        return _refused(e)
    except ValidationError as e:
        # validate_order_editable: the bill is being paid or already closed
        return JsonResponse({"error": " ".join(e.messages)}, status=400)
    except Exception:
        logger.exception("Error applying item discount to item #%s", item_id)
        return JsonResponse({"error": "Discount could not be applied. Please try again."}, status=500)


# -------------------------------------------------
# CLOSE A BILL WITHOUT PAYMENT ("payment bypass")
# -------------------------------------------------
# The guest walked out, the owner's own guest, a bill that will be settled
# another way: a manager or the owner closes the bill with money still owed.
# Since 3 Oct 2026 it is its own button on the bill page (it used to be the
# manager's back arrow, so going back to the tables closed the bill unpaid
# without a word), it needs a reason, and a manager's limit counts by the
# business day, not from midnight (a manager could close 3 before midnight
# and 3 more after). Each one is on the audit report with the amount unpaid.

BYPASS_DAILY_LIMIT = 3
ASK_BYPASS_REASON = "Give a reason for closing the bill without payment (for example: guest walked out)."


@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
def log_bypass(request, order_id):
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = {}
    try:
        reason = read_reason(data.get("reason") if isinstance(data, dict) else None, missing=ASK_BYPASS_REASON)
    except DiscountRefused as e:
        return _refused(e)

    try:
        with transaction.atomic():
            order = Order.objects.select_for_update().get(
                id=order_id,
                tenant=request.user.tenant,
                outlet=request.user.outlet
            )

            if order.status not in ("open", "billing"):
                return JsonResponse({"error": "This bill is already closed."
                                     if order.status in ("paid", "closed") else "This bill was cancelled."},
                                    status=400)

            if request.user.role != "owner":
                # One manager's closes, one at a time: lock their own row so
                # two quick taps can't both count 2 and both go through.
                from accounts.models import User
                User.objects.select_for_update().filter(pk=request.user.pk).first()
                from core.utils import get_business_date, get_business_date_range
                day_start, day_end = get_business_date_range(
                    get_business_date(timezone.now(), order.outlet), order.outlet)
                closed_today = OrderEvent.objects.filter(
                    tenant=request.user.tenant,
                    outlet=request.user.outlet,
                    created_by=request.user,
                    event_type="status_changed",
                    created_at__gte=day_start,
                    created_at__lt=day_end,
                ).filter(metadata__action="payment_gate_bypassed").count()

                if closed_today >= BYPASS_DAILY_LIMIT:
                    return JsonResponse({"error": (
                        f"You have closed {BYPASS_DAILY_LIMIT} bills without payment today, the most a "
                        "manager can. Ask the owner to close this one."
                    )}, status=403)

            from django.db.models import Sum
            paid = (order.payments.exclude(method="refund").aggregate(total=Sum("amount"))["total"]
                    or Decimal("0"))
            unpaid = max(order.grand_total - paid, Decimal("0"))

            logger.warning(
                "User %s closed order #%s without payment (unpaid %s)",
                request.user.username, order_id, unpaid,
            )

            # Actually close the order
            order.status = "closed"
            order.closed_at = timezone.now()
            order.save(update_fields=["status", "closed_at"])
            mark_ready_items_served(order)

            if order.table:
                order.table.state = "free"
                order.table.save(update_fields=["state"])

            OrderEvent.objects.create(
                tenant=order.tenant, outlet=order.outlet, order=order,
                event_type="status_changed",
                metadata={
                    "action": "payment_gate_bypassed",
                    "role": request.user.role,
                    "bypassed_by": request.user.username,
                    "reason": reason,
                    "unpaid": str(unpaid),
                    "paid": str(paid),
                },
                created_by=request.user
            )

        return JsonResponse({"success": True, "message": "Bill closed without payment"})

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception:
        logger.exception("Bypass error for order #%s", order_id)
        return JsonResponse({"error": "Payment bypass could not be processed. Please try again."}, status=500)
