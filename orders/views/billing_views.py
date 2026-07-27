# orders/views/billing_views.py
# ---------------------------------------------------------------------------
# SHIM - this file is kept for backwards compatibility.
# All logic has been moved to focused sub-modules:
#   billing_core.py   - billing_view, bill_view
#   payment_views.py  - pay_order, split_pay, refund_payment
#   discount_views.py - apply_discount, make_item_complimentary,
#                       apply_item_discount, log_bypass
#   print_views.py    - generate_bill, print_bill_action, print_kot_action,
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
# Re-exports - any code that still does `from orders.views.billing_views import X`
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
# and go through the same endpoint - 20/min is more than enough for either.
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
        # 1. QR Guest Case: Identify table and tenant via UUID token.
        # Falls back to Outlet.qr_token -- the outlet-wide "Counter /
        # Walk-in" QR for QSR/cafe outlets with no seating -- which
        # resolves tenant/outlet with table left as None, same as any
        # other walk-in order.
        from django.shortcuts import get_object_or_404
        from tenants.models import Outlet
        table = Table.objects.filter(qr_token=table_token).first()
        if table:
            tenant = table.tenant
            outlet = table.outlet
        else:
            outlet = get_object_or_404(Outlet, qr_token=table_token)
            tenant = outlet.tenant
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

    if table:
        # A QR code (or a staff-picked table) may point at a table that's
        # currently merged into another one — attach the order to the
        # merge's primary table so it lands in the same place a waiter's
        # order for this physical table would.
        from tablemerge.services import resolve_primary_table
        table = resolve_primary_table(table, tenant, outlet)

    # Validate phone BEFORE opening the transaction so a bad number returns a
    # clean 400 instead of a DB truncation error (which would leak column names).
    from core.validators import normalize_phone
    from django.core.exceptions import ValidationError as _PhoneError
    try:
        cust_phone = normalize_phone(data.get("customer_phone"))
    except _PhoneError:
        return JsonResponse(
            {"error": "Enter a valid 10-digit mobile number."}, status=400
        )

    try:
        with transaction.atomic():
            cust_name = data.get("customer_name")
            order_id = data.get("order_id")

            # A tableless guest (counter/walk-in QR, no login, table is None)
            # has no physical-table boundary to prove order_id ownership
            # with -- the outlet's counter QR token is shared by every
            # customer, unlike a table's, so a stored order_id can't be
            # trusted here. Skip straight to fresh-order creation below
            # instead of the ownership check further down, same as any
            # other non-dine_in source.
            if order_id and not (user is None and table is None):
                order = Order.objects.filter(
                    id=order_id, tenant=tenant, outlet=outlet
                ).first()
                if not order:
                    return JsonResponse({"error": "Order not found"}, status=404)

                # Guest (QR, no login) reordering more items into their table's
                # existing open order. A guest is only ever trusted for the ONE
                # table they scanned — without this check, a guest could pass
                # ANY order_id in this tenant/outlet (e.g. by guessing a nearby
                # number) and silently add items to a different table's bill.
                # Staff (user is not None) are already scoped to their own
                # outlet by the tenant/outlet filter above and may edit any
                # order in it, matching how running-order editing already works.
                if user is None and (not table or order.table_id != table.id):
                    return JsonResponse({"error": "That order does not belong to this table."}, status=403)

                if order.status not in ("open", "billing"):
                    return JsonResponse(
                        {"error": "This order has already been billed. Please call a waiter for more items."},
                        status=409,
                    )

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
            # Uses the row-locked DailyTokenCounter helper — NEVER MAX()+1,
            # which produces duplicate token numbers under concurrent load.
            # Every order gets a token for these tenants, not just table-less
            # ones — a QR scan always resolves a table, so restricting this to
            # table is None silently skipped every guest QR self-order, which
            # never showed up on the token dashboard or got a pickup number.
            if tenant and tenant.tenant_type in ['franchise', 'cafe']:
                from django.utils import timezone
                from core.utils import get_business_date
                from tokens.views import assign_counter_token
                if not hasattr(order, 'token'):
                    business_date = get_business_date(timezone.now(), outlet)
                    assign_counter_token(order, outlet, tenant, business_date)

            # Discounts may ONLY be applied by an authenticated staff member.
            # `source` is fully client-controlled, so it must not appear in this
            # gate — a QR guest could send source="takeaway" to unlock it and
            # then post discount_value="999999" to drive the total to zero
            # (recalculate_totals clamps the discount to the subtotal → free
            # food). Guests have user=None and never reach this branch.
            if user and user.role in ["owner", "manager", "cashier"]:
                d_type = data.get("discount_type")
                d_val = data.get("discount_value")
                if d_type in ["percentage", "amount"]:
                    try:
                        d_val = Decimal(str(d_val or 0))
                        # Reject negatives (would be an upcharge) and cap a
                        # percentage at 100 so a discount can never exceed the bill.
                        if d_val < 0:
                            d_val = Decimal("0")
                        if d_type == "percentage" and d_val > 100:
                            d_val = Decimal("100")
                        order.discount_type = d_type
                        order.discount_value = d_val
                    except (ValueError, TypeError, InvalidOperation):
                        pass

            order.save()
            add_items_to_order(user, order, cart, tenant=tenant, outlet=outlet)

            # Important: recalculate after adding items so the discount applies to the total
            order.recalculate_totals()

            u_name = user.username if user else "Guest (QR)"
            logger.info(
                "User %s created/updated order #%s on table %s source %s",
                u_name, order.id, table.name if table else "Walk-in/Online", source,
            )

        return JsonResponse({"success": True, "order_id": order.id})
    except Exception:
        # Never leak internal exception text (DB constraints, table names) to
        # the client — especially unauthenticated QR guests. Log full trace,
        # return a generic message.
        logger.exception("Order creation failed")
        return JsonResponse(
            {"error": "Could not create the order. Please try again."},
            status=400,
        )
