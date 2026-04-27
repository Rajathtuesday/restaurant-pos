# orders/views/billing_views.py
import json
import logging
import traceback
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, role_required
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem, Table, TableMerge, Payment
from shifts.models import CashSession
from orders.services.order_lock_service import lock_order
from orders.services.order_service import get_or_create_open_order, add_items_to_order
from orders.services.payment_service import process_payment
from orders.services.refund_service import process_refund
from orders.utils.payment_utils import validate_order_payment
from orders.utils.order_utils import validate_order_editable
from setup.models import PaymentConfig

logger = logging.getLogger("pos.orders")


# -------------------------------------------------
# BILLING PAGE
# -------------------------------------------------

@login_required
@tenant_required
def billing_view(request):
    table_id = request.GET.get("table")
    order = None

    if table_id:
        merge = (
            TableMerge.objects
            .filter(tenant=request.user.tenant, outlet=request.user.outlet,
                    is_active=True, tables__id=table_id)
            .select_related("primary_table").first()
        )
        if merge and str(table_id) != str(merge.primary_table.id):
            table_id = merge.primary_table.id

    if table_id:
        order = (
            Order.objects
            .filter(tenant=request.user.tenant, outlet=request.user.outlet,
                    table_id=table_id, status__in=["open", "billing"])
            .select_related("table", "lock").first()
        )

    if order:
        locked, locked_user = lock_order(order, request.user)
        if not locked:
            return render(request, "orders/order_locked.html", {"locked_by": locked_user, "order": order})

    categories = (
        MenuCategory.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet, is_active=True)
        .prefetch_related(Prefetch("items", queryset=MenuItem.objects.filter(is_available=True)))
    )

    tables = Table.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet, is_active=True
    ).order_by("name")

    return render(request, "orders/billing.html", {
        "categories": categories, "tables": tables,
        "order": order, "selected_table": table_id
    })


# -------------------------------------------------
# CREATE ORDER
# -------------------------------------------------

# @login_required -- Removed to allow QR guest ordering
@require_POST
# @tenant_required -- Removed to allow QR guest ordering
def create_order(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    cart = data.get("cart")
    table_id = data.get("table_id")
    table_token = data.get("table_token")
    source = data.get("source", "dine_in")
    aggregator_id = data.get("aggregator_id", "")
    discount_type = data.get("discount_type", "")
    discount_value = data.get("discount_value", 0)

    if not cart:
        return JsonResponse({"error": "Cart empty"}, status=400)

    tenant = None
    outlet = None
    table = None
    user = None

    if table_token:
        # 1. QR Guest Case: Identify table and tenant via UUID token
        from django.shortcuts import get_object_or_404
        table = get_object_or_404(Table, qr_token=table_token)
        tenant = table.tenant
        outlet = table.outlet
    elif request.user.is_authenticated:
        # 2. Staff Case: Logged in user from POS
        user = request.user
        tenant = user.tenant
        outlet = user.outlet
        if table_id:
            table = Table.objects.filter(
                id=table_id, tenant=tenant,
                outlet=outlet, is_active=True
            ).first()
            if not table:
                return JsonResponse({"error": "Invalid table"}, status=400)
    else:
        # 3. Unauthorized: No token and no user
        return JsonResponse({"error": "Unauthorized. Please scan a QR code."}, status=401)

    try:
        with transaction.atomic():
            # For 3rd party or takeaway, we always create a fresh order to avoid merging
            if source != "dine_in" or table is None:
                order = Order.objects.create(
                    tenant=tenant,
                    outlet=outlet,
                    table=table,
                    created_by=user,
                    status="open",
                    source=source,
                    aggregator_order_id=aggregator_id
                )
            else:
                order = get_or_create_open_order(user, table, tenant=tenant, outlet=outlet)
                order.source = source
                if aggregator_id:
                    order.aggregator_order_id = aggregator_id
            
            if discount_type in ["percentage", "amount"]:
                try:
                    val = Decimal(str(discount_value))
                    if val > 0:
                        order.discount_type = discount_type
                        order.discount_value = val
                except:
                    pass

            order.save()
            add_items_to_order(user, order, cart, tenant=tenant, outlet=outlet)

            # Important: recalculate after adding items so the discount applies to the total
            order.recalculate_totals()

            u_name = user.username if user else "Guest (QR)"
            logger.info(f"User {u_name} created/updated order #{order.id} on table {table.name if table else 'Walk-in/Online'} source {source}")

        return JsonResponse({"success": True, "order_id": order.id})
    except Exception as e:
        logger.exception(f"Order creation failed: {e}")
        return JsonResponse({"error": str(e)}, status=400)


# -------------------------------------------------
# BILL VIEW
# -------------------------------------------------

@login_required
@tenant_required
def bill_view(request, order_id):
    try:
        
        order = Order.objects.get(
            id=order_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
        
        if order.table:
            order.table.state = "billing"
            order.table.save(update_fields=["state"])
            
        # Get payment configuration
        config, _ = PaymentConfig.objects.get_or_create(
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        
        # Calculate remaining balance
        total_paid = order.payments.exclude(method="refund").aggregate(total=Sum("amount"))["total"] or Decimal("0")
        remaining = order.grand_total - total_paid
        
        return render(request, "orders/bill.html", {
            "order": order,
            "config": config,
            "remaining": remaining,
            "total_paid": total_paid
        })
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)


# -------------------------------------------------
# GENERATE BILL
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
def generate_bill(request, order_id):
    order = (
        Order.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet,
                id=order_id, status="open")
        .first()
    )
    if not order:
        return JsonResponse({"error": "No active order found"}, status=404)

    with transaction.atomic():
        order.status = "billing"
        order.save(update_fields=["status"])
        order.recalculate_totals()

    logger.info(f"User {request.user.username} generated bill for order #{order.id}")
    return JsonResponse({"success": True, "order_id": order.id})


# -------------------------------------------------
# PAYMENT
# -------------------------------------------------

@login_required
@require_POST
@tenant_required
def pay_order(request, order_id):
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

                if order.status in ["paid", "closed"]:
                    return JsonResponse({"error": "Order already completed"}, status=400)

                if order.grand_total == 0:
                    # Fully complimentary order!
                    order.status = "closed"
                    order.closed_at = timezone.now()
                    order.save(update_fields=["status", "closed_at"])
                    if order.table:
                        order.table.state = "cleaning"
                        order.table.save(update_fields=["state"])
                    logger.info(f"Order #{order.id} fully complimentary and closed")
                    return JsonResponse({"success": True, "message": "Complimentary order closed"})

                if amount == 0:
                    return JsonResponse({"error": "Amount must be greater than 0 for non-complimentary orders"}, status=400)

                # SECURITY: Ensure a Cash Session is open before accepting payment
                active_session = CashSession.objects.filter(
                    tenant=request.user.tenant, 
                    outlet=request.user.outlet, 
                    status="open"
                ).exists()
                
                if not active_session:
                    return JsonResponse({
                        "error": "No open cash session. Please open a session in Shift Management first."
                    }, status=400)

                process_payment(order, method, amount, request.user)

                logger.info(f"User {request.user.username} recorded {method} payment of ₹{amount} for order #{order.id}")

                OrderEvent.objects.create(
                    tenant=order.tenant, outlet=order.outlet, order=order,
                    event_type="payment_added",
                    amount=amount,
                    metadata={"method": method, "amount": str(amount)},
                    created_by=request.user
                )

                order.refresh_from_db()

                if order.status == "paid":
                    validate_order_payment(order)
                    order.status = "closed"
                    order.closed_at = timezone.now()
                    order.save(update_fields=["status", "closed_at"])
                    if order.table:
                        order.table.state = "cleaning"
                        order.table.save(update_fields=["state"])
                    logger.info(f"Order #{order.id} fully paid and closed")
                    return JsonResponse({"success": True, "message": "Payment complete, order closed"})

                remaining = order.grand_total - (
                    order.payments.exclude(method="refund").aggregate(total=Sum("amount"))["total"] or Decimal("0")
                )
                return JsonResponse({"success": True, "message": "Partial payment recorded", "remaining": remaining})

        except Exception as e:
            logger.error(f"Payment error for order #{order_id}: {e}")
            # Get the exact error message from ValidationError if possible
            err_msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return JsonResponse({"error": err_msg}, status=400)

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)


# -------------------------------------------------
# DISCOUNT
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "cashier", "owner")
def apply_discount(request, order_id):

    try:
        data = json.loads(request.body)
        discount_type = data.get("type", "percentage")
        value = Decimal(str(data.get("value", 0)))

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
            if order.status in ["paid", "closed"]:
                raise Exception("Order is already fully paid or closed")

            order.discount_type = discount_type
            order.discount_value = value
            order.save(update_fields=["discount_type", "discount_value"])
            order.recalculate_totals()

            logger.warning(f"User {request.user.username} applied {discount_type} discount of {value} to order #{order_id}")

            OrderEvent.objects.create(
                tenant=order.tenant, outlet=order.outlet, order=order,
                event_type="status_changed",
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
        logger.exception("Error applying discount")
        err_msg = str(e) if str(e) else "Internal Server Error"
        return JsonResponse({"error": err_msg}, status=500)


# -------------------------------------------------
# COMPLIMENTARY ITEM
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
def make_item_complimentary(request, item_id):

    try:
        item = (
            OrderItem.objects.select_related("order")
            .get(id=item_id, order__tenant=request.user.tenant, order__outlet=request.user.outlet)
        )
        validate_order_editable(item.order)
        item.is_complimentary = True
        item.save(update_fields=["is_complimentary"])
        item.order.recalculate_totals()
        logger.warning(f"User {request.user.username} marked item #{item_id} as complimentary")
        return JsonResponse({"success": True})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)


# -------------------------------------------------
# REFUND PAYMENT  (Manager/Owner only)
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
def refund_payment(request, payment_id):
    try:

        data = json.loads(request.body)
        amount = data.get("amount")
        reason = data.get("reason", "Manager refund")

        if not amount:
            return JsonResponse({"error": "Amount required"}, status=400)

        payment = Payment.objects.select_related("order").get(
            id=payment_id,
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet
        )

        with transaction.atomic():
            refund = process_refund(payment.order, payment_id, amount, request.user)

        logger.warning(f"User {request.user.username} issued refund of ₹{amount} for payment #{payment_id}")
        return JsonResponse({"success": True, "refund_id": refund.id, "amount": str(refund.amount)})

    except Exception as e:
        logger.exception("Error refunding payment")
        err_msg = str(e) if str(e) else "Internal Server Error"
        return JsonResponse({"error": err_msg}, status=500)


# -------------------------------------------------
# PER-ITEM DISCOUNT  (Manager/Owner only)
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
@role_required("manager", "cashier", "owner")
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

            # Recalculate item total after per-line discount
            base_total = item.price * item.quantity
            discounted = base_total * (1 - discount_pct / 100)
            item.total_price = discounted.quantize(Decimal("0.01"))
            item.notes = (item.notes or "") + f" [Discount: {discount_pct}%]"
            item.save(update_fields=["total_price", "notes"])
            item.order.recalculate_totals()

            OrderEvent.objects.create(
                tenant=item.order.tenant, outlet=item.order.outlet, order=item.order,
                event_type="item_updated",
                metadata={"item_id": item.id, "discount_pct": str(discount_pct)},
                created_by=request.user
            )

        logger.warning(f"User {request.user.username} applied {discount_pct}% discount to item #{item_id}")
        return JsonResponse({"success": True, "new_total": float(item.order.grand_total)})

    except Exception as e:
        logger.exception("Error applying item discount")
        err_msg = str(e) if str(e) else "Internal Server Error"
        return JsonResponse({"error": err_msg}, status=500)


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
                today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
                bypass_count = OrderEvent.objects.filter(
                    tenant=request.user.tenant,
                    outlet=request.user.outlet,
                    created_by=request.user,
                    event_type="status_changed",
                    created_at__gte=today
                ).filter(metadata__action="payment_gate_bypassed").count()

                if bypass_count >= 3:
                    return JsonResponse({"error": "Daily payment bypass limit (3) reached. Contact owner."}, status=403)

            logger.warning(
                f"User {request.user.username} bypassed payment gate for order #{order_id}"
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
    except Exception as e:
        logger.error(f"Bypass error for order #{order_id}: {e}")
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@role_required("manager", "cashier", "owner")
def print_bill_action(request, order_id):
    """Triggers the physical thermal printer for a bill in the background."""
    import threading
    from orders.services.printing_service import PrintingService
    try:
        order = Order.objects.get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
        
        # Table does not have a .station attribute — get printer from setup config
        from setup.models import PaymentConfig
        printer_host = None

        # Attempt to get outlet-level printer IP from setup if you store it there
        # For now fall back to localhost (local print agent)
        if not printer_host:
            printer_host = "127.0.0.1"
        
        # TODO: Implement proper Celery task for printing
        # For now, execute synchronously to prevent daemon threads getting killed silently
        printer = PrintingService(printer_type="network", host=printer_host) 
        printer.print_bill(order)
        
        return JsonResponse({"success": True, "message": "Printing initiated"})
            
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        logger.exception("Error initiating print")
        return JsonResponse({"error": str(e)}, status=500)


# -------------------------------------------------
# SPLIT BILL  — POST /orders/<id>/split-pay/
# -------------------------------------------------

@login_required
@require_POST
@tenant_required
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

    if people < 2:
        return JsonResponse({"error": "'people' must be 2 or more"}, status=400)

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

            if order.status == "paid":
                order.status = "closed"
                order.closed_at = timezone.now()
                order.save(update_fields=["status", "closed_at"])
                if order.table:
                    order.table.state = "cleaning"
                    order.table.save(update_fields=["state"])

        logger.info(
            f"User {request.user.username} split order #{order_id} "
            f"across {people} people via {method}"
        )
        return JsonResponse({
            "success": True,
            "people": people,
            "shares": [float(s) for s in shares],
            "order_status": order.status,
        })

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        logger.exception(f"Split-pay error for order #{order_id}")
        err_msg = e.messages[0] if hasattr(e, "messages") else str(e)
        return JsonResponse({"error": err_msg}, status=400)
