# orders/views/billing_views.py
# ---------------------------------------------------------------------------
# SHIM — this file is kept for backwards compatibility.
# All logic has been moved to focused sub-modules:
#   billing_core.py   — billing_view, bill_view
#   payment_views.py  — pay_order, split_pay, refund_payment
#   discount_views.py — apply_discount, make_item_complimentary,
#                       apply_item_discount, log_bypass
#   print_views.py    — generate_bill, print_bill_action, print_kot_action,
#                       printer_status, download_pdf_bill
#
# create_order lives here because it spans billing + order creation concerns
# and is still imported directly from this module by orders/urls.py.
# ---------------------------------------------------------------------------

import json
import logging
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from orders.models import Order, Table
from orders.services.order_service import get_or_create_open_order, add_items_to_order

logger = logging.getLogger("pos.orders")

# ---------------------------------------------------------------------------
# Re-exports — any code that still does `from orders.views.billing_views import X`
# will continue to work without modification.
# ---------------------------------------------------------------------------
from .billing_core import billing_view, bill_view  # noqa: F401
from .payment_views import pay_order, split_pay, refund_payment  # noqa: F401
from .discount_views import (  # noqa: F401
    apply_discount, make_item_complimentary, apply_item_discount, log_bypass,
)
from .print_views import (  # noqa: F401
    generate_bill, print_bill_action, print_kot_action, printer_status, download_pdf_bill,
)

__all__ = [
    # page renders
    "billing_view",
    "bill_view",
    # order creation (lives here)
    "create_order",
    # payment
    "pay_order",
    "split_pay",
    "refund_payment",
    # discounts / adjustments
    "apply_discount",
    "make_item_complimentary",
    "apply_item_discount",
    "log_bypass",
    # printing / bill generation
    "generate_bill",
    "print_bill_action",
    "print_kot_action",
    "printer_status",
    "download_pdf_bill",
]


# -------------------------------------------------
# CREATE ORDER
# -------------------------------------------------

# @login_required -- Removed to allow QR guest ordering
# Rate-limit BEFORE require_POST so the check runs before any body parsing.
# 20 requests/minute per IP. Guests hit this via QR; staff calls are authenticated
# and go through the same endpoint — 20/min is more than enough for either.
@ratelimit(key="ip", rate="20/m", method="POST", block=True)
@require_POST
# @tenant_required -- Removed to allow QR guest ordering
def create_order(request):
    """
    API endpoint for creating new orders or updating existing ones.
    Accepts JSON payloads from both the POS dashboard (Staff) and Digital Menu (Guest QR).
    Automatically resolves the correct tenant and outlet via user session or QR token.
    Applies optional discounts and triggers recalculation of all financial totals.

    Rate-limited to 20 POST requests per minute per IP address to prevent
    malformed-cart spam from exhausting inventory and DB write capacity.
    """
    # django-ratelimit sets request.limited=True and raises Ratelimited when block=True,
    # but we still return a JSON 429 for API clients that expect JSON.
    if getattr(request, "limited", False):
        return JsonResponse(
            {"error": "Too many requests. Please slow down."},
            status=429
        )
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
            cust_name = data.get("customer_name")
            cust_phone = data.get("customer_phone")
            order_id = data.get("order_id")

            if order_id:
                order = Order.objects.filter(
                    id=order_id, tenant=tenant, outlet=outlet
                ).first()
                if not order:
                    return JsonResponse({"error": "Order not found"}, status=404)

                order.source = source
                if aggregator_id: order.aggregator_order_id = aggregator_id
                if cust_name: order.customer_name = cust_name
                if cust_phone: order.customer_phone = cust_phone

            # For 3rd party or takeaway, we always create a fresh order to avoid merging
            elif source != "dine_in" or table is None:
                order = Order.objects.create(
                    tenant=tenant,
                    outlet=outlet,
                    table=table,
                    created_by=user,
                    status="open",
                    source=source,
                    aggregator_order_id=aggregator_id,
                    customer_name=cust_name,
                    customer_phone=cust_phone
                )
            else:
                order = get_or_create_open_order(user, table, tenant=tenant, outlet=outlet)
                order.source = source
                if aggregator_id:
                    order.aggregator_order_id = aggregator_id
                if cust_name:
                    order.customer_name = cust_name
                if cust_phone:
                    order.customer_phone = cust_phone

            # Franchise / Cafe Token Generation
            if tenant and tenant.tenant_type in ['franchise', 'cafe'] and table is None:
                from orders.models import TokenOrder
                from django.utils import timezone
                from django.db.models import Max
                if not hasattr(order, 'token'):
                    today = timezone.localdate()
                    max_token = TokenOrder.objects.filter(
                        outlet=outlet, date=today
                    ).aggregate(Max('token_number'))['token_number__max'] or 0
                    TokenOrder.objects.create(
                        tenant=tenant,
                        outlet=outlet,
                        order=order,
                        token_number=max_token + 1,
                        date=today
                    )

            # Allow discount application during creation for aggregators or staff
            if (source != "dine_in" or (user and user.role in ["owner", "manager", "cashier"])):
                d_type = data.get("discount_type")
                d_val = data.get("discount_value")
                if d_type in ["percentage", "amount"]:
                    from decimal import InvalidOperation
                    try:
                        order.discount_type = d_type
                        order.discount_value = Decimal(str(d_val or 0))
                    except (ValueError, TypeError, InvalidOperation):
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
