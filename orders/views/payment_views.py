# orders/views/payment_views.py
import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from core.celery_utils import dispatch
from core.decorators import tenant_required, role_required, feature_required
from orders.models import Order, OrderEvent, Payment
from shifts.models import CashSession
from orders.services.payment_service import process_payment, mark_ready_items_served
from payments.refund_service import process_refund
from setup.models import PaymentConfig

logger = logging.getLogger("pos.orders")


# -------------------------------------------------
# PAYMENT
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "cashier", "captain", "owner")
@ratelimit(key="user", rate="15/m", method="POST", block=True)
def pay_order(request, order_id):
    """
    Processes a payment for an order and links it to the active cash session.
    Enforces outlet-level payment method configurations (e.g., if UPI is disabled).
    Supports partial payments and auto-closes the order if the remaining balance hits zero.
    """
    try:
        data = json.loads(request.body)
        method = data.get("method")
        amount = data.get("amount")

        VALID_METHODS = ["cash", "upi", "card"]
        if method not in VALID_METHODS:
            return JsonResponse({"error": "Invalid payment method"}, status=400)

        # Enforce outlet-level PaymentConfig
        try:
            config = PaymentConfig.objects.get(
                tenant=request.user.tenant,
                outlet=request.user.outlet
            )
            method_enabled = {
                "cash": config.cash_enabled,
                "upi":  config.upi_enabled,
                "card": config.card_enabled,
            }
            if not method_enabled.get(method, False):
                return JsonResponse(
                    {"error": f"Payment method '{method}' is not enabled for this outlet."},
                    status=400
                )
        except PaymentConfig.DoesNotExist:
            return JsonResponse(
                {"error": "Payment methods not configured for this outlet. Please configure them in setup."},
                status=400
            )
        if amount is None:
            return JsonResponse({"error": "Amount required"}, status=400)
        try:
            amount = Decimal(str(amount))
        except InvalidOperation:
            return JsonResponse({"error": "Invalid amount"}, status=400)
        if amount < 0:
            return JsonResponse({"error": "Amount cannot be negative"}, status=400)

        try:
            with transaction.atomic():
                order = (
                    Order.objects.select_for_update()
                    .get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
                )

                if order.status in ["paid", "closed", "cancelled"]:
                    return JsonResponse({"error": "Order already completed"}, status=400)

                # ── Auto-KOT mode ────────────────────────────────────────────
                # Two independent reasons a KOT should fire at payment time
                # instead of earlier:
                #   1. Tenant has no KDS and no separate kitchen printer --
                #      there's nowhere else to send it before the bill anyway.
                #   2. This is a token order (QSR/cafe counter) -- pay-first,
                #      cook-after is the whole model there, regardless of
                #      whether the outlet also happens to run a kitchen
                #      display. Token Billing's per-item approve action
                #      (orders/views/order_views.py::approve_item) deliberately
                #      does NOT create a KOT itself for exactly this reason --
                #      payment is the one and only KOT-firing moment for a
                #      token order.
                from core.features import has_feature
                from setup.models import KitchenStation as _KS
                _has_kds = has_feature(order.tenant, "kitchen_display")
                _has_kt  = _KS.objects.filter(
                    tenant=order.tenant, outlet=order.outlet,
                    is_active=True, is_default=False,
                ).exclude(printer_ip__isnull=True).exclude(printer_ip="").exists()
                _is_token_order = hasattr(order, "token")
                _auto_kot = _is_token_order or (not _has_kds and not _has_kt)

                if _auto_kot:
                    pending = order.items.filter(status="pending")
                    if pending.exists():
                        try:
                            from kitchen.services.kot_service import create_kot
                            create_kot(request.user, order, print_on_create=False)
                            order.refresh_from_db()
                        except Exception as _ke:
                            logger.warning("Auto-KOT creation failed in pay_order: %s", _ke)

                if order.grand_total == 0:
                    # Fully complimentary order!
                    order.status = "closed"
                    order.closed_at = timezone.now()
                    order.save(update_fields=["status", "closed_at"])
                    mark_ready_items_served(order)
                    if order.table:
                        order.table.state = "free"
                        order.table.save(update_fields=["state"])
                    logger.info("Order #%s fully complimentary and closed", order.id)
                    return JsonResponse({"success": True, "message": "Complimentary order closed"})

                if amount == 0:
                    return JsonResponse({"error": "Amount must be greater than 0 for non-complimentary orders"}, status=400)

                # Ensure a Cash Session is open before accepting payment.
                # QSR counters auto-get a session on first payment of the day.
                # Fine dining staff should open sessions manually via Shifts.
                active_session = CashSession.objects.filter(
                    tenant=request.user.tenant,
                    outlet=request.user.outlet,
                    status="open"
                ).first()

                if not active_session:
                    tenant_type = getattr(request.user.tenant, "tenant_type", "")
                    if tenant_type in ("franchise", "cafe"):
                        # QSR/Café: auto-open a session so payment is never blocked
                        active_session = CashSession.objects.create(
                            tenant=request.user.tenant,
                            outlet=request.user.outlet,
                            status="open",
                            opened_by=request.user,
                            opening_balance=Decimal("0"),
                        )
                        logger.info(
                            "Auto-created cash session #%s for QSR outlet %s",
                            active_session.id, request.user.outlet.name,
                        )
                    else:
                        # Fine dining: require manual session opening for proper shift tracking
                        return JsonResponse({
                            "error": "No open cash session. Please open a session in Shifts → Sessions."
                        }, status=400)

                payment_result = process_payment(order, method, amount, request.user)
                change_due = payment_result.get("change_due", Decimal("0.00"))

                logger.info(
                    "User %s recorded %s payment of Rs.%s for order #%s",
                    request.user.username, method, amount, order.id,
                )

                OrderEvent.objects.create(
                    tenant=order.tenant, outlet=order.outlet, order=order,
                    event_type="payment_added",
                    amount=amount,
                    metadata={"method": method, "amount": str(amount), "change_due": str(change_due)},
                    created_by=request.user
                )

                order.refresh_from_db()

                if payment_result["order_closed"]:
                    # payment_service already set status=closed and closed_at.
                    # Set table to free here - service doesn't know about tables.
                    if order.table:
                        order.table.state = "free"
                        order.table.save(update_fields=["state"])
                    logger.info("Order #%s fully paid and closed", order.id)

                    # Inventory deduction is handled by payment_service._deduct_inventory_for_order()
                    # which runs for all order.source == "counter" orders automatically.
                    # No additional deduction needed here.

                    # Auto-print: in auto-KOT mode queue bill+KOTs immediately after
                    # payment so cashier doesn't need to click Print manually.
                    if _auto_kot:
                        _oid = order.id
                        def _auto_print():
                            try:
                                from orders.tasks import print_bill_task
                                from setup.services.station_service import get_default_station
                                from orders.models import Order as _O
                                _ord = _O.objects.get(id=_oid)
                                _st  = get_default_station(_ord.created_by or request.user)
                                if _st and _st.printer_ip:
                                    dispatch(print_bill_task, _oid, _st.id)
                            except Exception as _pe:
                                logger.warning("Auto-print after payment failed: %s", _pe)
                        transaction.on_commit(_auto_print)

                    # WhatsApp receipt - fire after commit so it doesn't run on rollback
                    _order_id = order.id
                    _order_ref = order  # captured for closure
                    def _send_whatsapp():
                        try:
                            from core.features import has_feature
                            if not has_feature(_order_ref.tenant, "whatsapp_receipts"):
                                return
                            from notifications.tasks import send_whatsapp_receipt_task
                            from orders.views.public_views import make_public_bill_token
                            from django.urls import reverse
                            # Sign + build the absolute URL here, synchronously — the
                            # Celery task has no `request` object to build it from.
                            token = make_public_bill_token(_order_id)
                            bill_url = request.build_absolute_uri(reverse("public-bill", args=[token]))
                            dispatch(send_whatsapp_receipt_task, _order_id, bill_url)
                        except Exception as _e:
                            logger.error("WhatsApp receipt dispatch failed for order %s: %s", _order_id, _e)
                    transaction.on_commit(_send_whatsapp)

                    return JsonResponse({
                        "success": True,
                        "message": "Payment complete, order closed",
                        "change_due": float(change_due),
                        "auto_kot": _auto_kot,
                        "order_number": order.order_number or str(order.id),
                    })

                remaining = payment_result["remaining"]
                return JsonResponse({
                    "success": True,
                    "message": "Partial payment recorded",
                    "remaining": float(remaining),
                    "change_due": float(change_due)
                })

        except ValidationError as e:
            return JsonResponse({"error": e.messages[0]}, status=400)
        except Order.DoesNotExist:
            return JsonResponse({"error": "Order not found"}, status=404)
        except Exception:
            logger.exception("Payment error for order #%s", order_id)
            return JsonResponse({"error": "Payment could not be processed. Please try again."}, status=500)

    except Exception:
        logger.exception("Unexpected error in pay_order for order #%s", order_id)
        return JsonResponse({"error": "Payment could not be processed. Please try again."}, status=500)


# -------------------------------------------------
# REFUND PAYMENT  (Manager/Owner only)
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
@ratelimit(key="user", rate="5/m", method="POST", block=True)
def refund_payment(request, payment_id):
    try:

        data = json.loads(request.body)
        amount               = data.get("amount")
        customer_complaint   = data.get("customer_complaint", "").strip()
        reason               = data.get("reason", "").strip()

        if not amount:
            return JsonResponse({"error": "Amount required"}, status=400)
        if not reason:
            return JsonResponse({"error": "Manager note is required"}, status=400)

        payment = Payment.objects.select_related("order").get(
            id=payment_id,
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet
        )

        with transaction.atomic():
            refund = process_refund(
                payment.order, payment_id, amount, request.user,
                reason=reason,
                customer_complaint=customer_complaint,
            )

        logger.warning(
            "User %s issued refund of Rs.%s for payment #%s",
            request.user.username, amount, payment_id,
        )
        return JsonResponse({"success": True, "refund_id": refund.id, "amount": str(refund.amount)})

    except Exception as e:
        logger.exception("Error refunding payment #%s", payment_id)
        return JsonResponse({"error": "Refund could not be processed. Please try again."}, status=500)


# -------------------------------------------------
# SPLIT BILL  - POST /orders/<id>/split-pay/
# -------------------------------------------------

@login_required
@require_POST
@tenant_required
@role_required("manager", "cashier", "captain", "owner")
@feature_required("split_bill")
def split_pay(request, order_id):
    """
    Splits the order grand_total evenly across N people and records
    a Payment for each share using process_payment().

    Expected JSON body:
        { "people": 3, "method": "cash" }

    The last person absorbs any rounding remainder so the sum always
    equals grand_total exactly.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        people = int(data.get("people", 0))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid value for 'people'"}, status=400)

    if people < 2 or people > 20:
        return JsonResponse({"error": "'people' must be between 2 and 20"}, status=400)

    method = data.get("method")
    VALID_METHODS = ["cash", "upi", "card"]
    if method not in VALID_METHODS:
        return JsonResponse({"error": "Invalid payment method"}, status=400)

    try:
        config = PaymentConfig.objects.get(
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        method_enabled = {
            "cash": config.cash_enabled,
            "upi":  config.upi_enabled,
            "card": config.card_enabled,
        }
        if not method_enabled.get(method, False):
            return JsonResponse(
                {"error": f"Payment method '{method}' is not enabled for this outlet."},
                status=400
            )
    except PaymentConfig.DoesNotExist:
        return JsonResponse(
            {"error": "Payment methods not configured for this outlet."},
            status=400
        )

    from orders.services.split_service import split_bill

    # SECURITY: Ensure a Cash Session is open before accepting split payment
    active_session = CashSession.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        status="open"
    ).exists()

    if not active_session:
        return JsonResponse({
            "error": "No open cash session. Please open a session in Shift Management first."
        }, status=400)

    try:
        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
            )

            if order.status in ["paid", "closed", "cancelled"]:
                return JsonResponse({"error": "Order is not payable"}, status=400)

            if order.grand_total <= 0:
                return JsonResponse({"error": "Order has no balance due"}, status=400)

            shares = split_bill(order, people)

            for share in shares:
                process_payment(order, method, share, request.user)

            order.refresh_from_db()

            OrderEvent.objects.create(
                tenant=order.tenant, outlet=order.outlet, order=order,
                event_type="payment_added",
                amount=order.grand_total,
                metadata={"action": "split_bill", "people": people, "method": method},
                created_by=request.user,
            )

            # payment_service already closed the order - set table state here.
            if order.status == "closed":
                if order.table:
                    order.table.state = "free"
                    order.table.save(update_fields=["state"])

        logger.info(
            "User %s split order #%s across %s people via %s",
            request.user.username, order_id, people, method,
        )
        return JsonResponse({
            "success": True,
            "people": people,
            "shares": [float(s) for s in shares],
            "order_status": order.status,
        })

    except ValidationError as e:
        return JsonResponse({"error": e.messages[0]}, status=400)
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception:
        logger.exception("Split-pay error for order #%s", order_id)
        return JsonResponse({"error": "Payment could not be processed. Please try again."}, status=500)
