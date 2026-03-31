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

from core.decorators import tenant_required
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem, Table, TableMerge
from orders.services.order_lock_service import lock_order
from orders.utils.order_utils import validate_order_editable

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
            .select_related("table").first()
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

@login_required
@require_POST
@tenant_required
def create_order(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    cart = data.get("cart")
    table_id = data.get("table_id")

    if not cart:
        return JsonResponse({"error": "Cart empty"}, status=400)

    table = None
    if table_id:
        table = Table.objects.filter(
            id=table_id, tenant=request.user.tenant,
            outlet=request.user.outlet, is_active=True
        ).first()
        if not table:
            return JsonResponse({"error": "Invalid table"}, status=400)

    try:
        with transaction.atomic():
            from orders.services.order_service import get_or_create_open_order, add_items_to_order
            order = get_or_create_open_order(request.user, table)
            add_items_to_order(request.user, order, cart)

            logger.info(f"User {request.user.username} created/updated order #{order.id} on table {table.name if table else 'Walk-in'}")

            OrderEvent.objects.create(
                tenant=order.tenant, outlet=order.outlet, order=order,
                event_type="item_added",
                metadata={"cart_count": len(cart)},
                created_by=request.user
            )

        return JsonResponse({"success": True, "order_id": order.id})

    except Exception as e:
        print(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


# -------------------------------------------------
# BILL VIEW
# -------------------------------------------------

@login_required
@tenant_required
def bill_view(request, order_id):
    try:
        from setup.models import PaymentConfig
        
        order = Order.objects.get(
            id=order_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
        
        # Ensure totals are fresh
        order.recalculate_totals()
        
        if order.table:
            order.table.state = "billing"
            order.table.save(update_fields=["state"])
            
        # Get payment configuration
        config, _ = PaymentConfig.objects.get_or_create(
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        
        # Calculate remaining balance
        total_paid = order.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
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
def generate_bill(request, table_id):
    order = (
        Order.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet,
                table_id=table_id, status="open")
        .first()
    )
    if not order:
        return JsonResponse({"error": "No active order for this table"}, status=404)

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

        if method not in ["cash", "upi", "card"]:
            return JsonResponse({"error": "Invalid payment method"}, status=400)
        if amount is None:
            return JsonResponse({"error": "Amount required"}, status=400)
        try:
            amount = Decimal(str(amount))
        except InvalidOperation:
            return JsonResponse({"error": "Invalid amount"}, status=400)
        if amount <= 0:
            return JsonResponse({"error": "Amount must be positive"}, status=400)

        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
            )
            if order.status in ["paid", "closed"]:
                return JsonResponse({"error": "Order already completed"}, status=400)

            from orders.services.payment_service import process_payment
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
                from orders.utils.payment_utils import validate_order_payment
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
                order.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
            )
            return JsonResponse({"success": True, "message": "Partial payment recorded", "remaining": remaining})

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


# -------------------------------------------------
# DISCOUNT
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
def apply_discount(request, order_id):
    if request.user.role not in ["manager", "cashier", "owner"]:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
        percent = float(data.get("percent", 0))

        if percent < 0 or percent > 100:
            return JsonResponse({"error": "Invalid percentage"}, status=400)

        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
            )
            if order.status in ["paid", "closed"]:
                raise Exception("Order is already fully paid or closed")

            order.discount_type = "percentage"
            order.discount_value = percent
            order.save(update_fields=["discount_type", "discount_value"])
            order.recalculate_totals()

            logger.warning(f"User {request.user.username} applied {percent}% discount to order #{order_id}")

            OrderEvent.objects.create(
                tenant=order.tenant, outlet=order.outlet, order=order,
                event_type="status_changed",
                metadata={"action": "discount_applied", "percent": percent},
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
        return JsonResponse({"error": str(e)}, status=400)


# -------------------------------------------------
# COMPLIMENTARY ITEM
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
def make_item_complimentary(request, item_id):
    if request.user.role not in ["manager", "owner"]:
        return JsonResponse({"error": "Permission denied"}, status=403)

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
def refund_payment(request, payment_id):
    if request.user.role not in ["manager", "owner"] and not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        from orders.models import Payment
        from orders.services.refund_service import process_refund

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
        return JsonResponse({"error": str(e)}, status=400)


# -------------------------------------------------
# PER-ITEM DISCOUNT  (Manager/Owner only)
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
def apply_item_discount(request, item_id):
    if request.user.role not in ["manager", "cashier", "owner"] and not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

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
        return JsonResponse({"error": str(e)}, status=400)
