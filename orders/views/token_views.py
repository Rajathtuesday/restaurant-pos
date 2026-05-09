# orders/views/token_views.py
# ============================================================
# WHY DailyTokenCounter instead of MAX(token_number)+1:
#   Aggregate queries CANNOT be locked with select_for_update().
#   Two concurrent requests both read MAX=5 → both create token 6
#   → second INSERT crashes on unique_together → order silently lost.
#   A counter ROW can be locked, guaranteeing sequential uniqueness.
#
# WHY get_business_date() instead of timezone.localdate():
#   DB server runs UTC.  A 1 AM IST order gets date = previous UTC
#   calendar day if we use localdate() without tz awareness.
#   get_business_date() also respects outlet.business_day_start_hour.
#
# WHY Http404 in token_billing (not JsonResponse):
#   This is a page-render view.  Returning raw JSON on a page request
#   means Django's 404.html never renders and the user sees {"error":…}.
# ============================================================

import json
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required
from core.utils import get_business_date
from menu.models import MenuCategory, MenuItem
from orders.models import Order, DailyTokenCounter, TokenOrder
from setup.models import PaymentConfig

logger = logging.getLogger("pos.orders")


# ------------------------------------------------------------------
# TOKEN DASHBOARD
# ------------------------------------------------------------------

@login_required
@tenant_required
@feature_required("token_system")
def token_dashboard(request):
    """
    Main screen for franchise and cafe ordering.
    Shows today's active tokens and revenue stats.
    Fine-dining tenants are blocked at decorator level.
    """
    outlet = request.user.outlet
    today  = get_business_date(timezone.now(), outlet)

    active_tokens = (
        TokenOrder.objects
        .filter(outlet=outlet, date=today, order__status__in=["open", "billing"])
        .select_related("order")
        .order_by("token_number")
    )

    today_revenue = (
        Order.objects
        .filter(outlet=outlet, token__date=today, status__in=["closed", "paid"])
        .aggregate(total=Sum("grand_total"))["total"] or Decimal("0")
    )

    closed_tokens = TokenOrder.objects.filter(
        outlet=outlet, date=today, order__status__in=["closed", "paid"]
    ).count()

    # Peek at counter for display only (no lock needed — approximate is fine)
    try:
        counter   = DailyTokenCounter.objects.get(outlet=outlet, date=today)
        next_token = counter.value + 1
    except DailyTokenCounter.DoesNotExist:
        from django.db.models import Max
        max_existing = TokenOrder.objects.filter(
            outlet=outlet, date=today
        ).aggregate(max_val=Max("token_number"))["max_val"]
        next_token = (max_existing or 0) + 1

    return render(request, "orders/token_dashboard.html", {
        "active_tokens": active_tokens,
        "next_token":    next_token,
        "today_revenue": today_revenue,
        "closed_tokens": closed_tokens,
        "today":         today,
    })


# ------------------------------------------------------------------
# CREATE TOKEN ORDER  (POST only)
# ------------------------------------------------------------------

@login_required
@tenant_required
@feature_required("token_system")
@require_POST
def create_token_order(request):
    """
    Creates a new token order for franchise or cafe.

    Token number is assigned via DailyTokenCounter row-lock — the only
    safe pattern when two staff members hit "New Order" simultaneously.
    """
    tenant = request.user.tenant
    outlet = request.user.outlet

    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}

    customer_name  = (body.get("customer_name",  "") or "").strip() or None
    customer_phone = (body.get("customer_phone", "") or "").strip() or None

    try:
        with transaction.atomic():
            # -------------------------------------------------------
            # 1.  Business date — respects outlet's start-of-day hour.
            # -------------------------------------------------------
            business_date = get_business_date(timezone.now(), outlet)

            # -------------------------------------------------------
            # 2.  Lock the counter row.
            #     get_or_create inside select_for_update is safe here
            #     because of the atomic block wrapping it.
            # -------------------------------------------------------
            counter, created = (
                DailyTokenCounter.objects
                .select_for_update()
                .get_or_create(
                    outlet=outlet,
                    tenant=tenant,
                    date=business_date,
                    defaults={"value": 0},
                )
            )
            
            # SELF-HEAL: If the counter row was missing (e.g. manually deleted in Admin)
            # but token orders already exist for today, sync the counter to the max token.
            if created:
                from django.db.models import Max
                max_existing = TokenOrder.objects.filter(
                    outlet=outlet, date=business_date
                ).aggregate(max_val=Max("token_number"))["max_val"]
                if max_existing:
                    counter.value = max_existing
                    
            counter.value += 1
            counter.save(update_fields=["value"])
            next_token = counter.value

            # -------------------------------------------------------
            # 3.  Create Order.  source="counter" maps to the QSR
            #     bucket in reports — not "dine_in" or "takeaway".
            # -------------------------------------------------------
            order = Order.objects.create(
                tenant=tenant,
                outlet=outlet,
                table=None,
                created_by=request.user,
                status="open",
                source="counter",
                customer_name=customer_name,
                customer_phone=customer_phone,
            )

            # -------------------------------------------------------
            # 4.  Create TokenOrder with explicit date (no auto_now).
            # -------------------------------------------------------
            TokenOrder.objects.create(
                tenant=tenant,
                outlet=outlet,
                order=order,
                token_number=next_token,
                date=business_date,
            )

        logger.info(
            "Token #%s created | order=%s | outlet=%s | user=%s",
            next_token, order.id, outlet.id, request.user.username,
        )

        return JsonResponse({
            "success":      True,
            "order_id":     order.id,
            "token_number": next_token,
        })

    except Exception as e:
        logger.exception("Token order creation failed: %s", e)
        return JsonResponse({"success": False, "error": str(e)}, status=400)


# ------------------------------------------------------------------
# TOKEN BILLING SCREEN
# ------------------------------------------------------------------

@login_required
@tenant_required
@feature_required("token_system")
def token_billing(request, order_id):
    """
    Billing screen for a token order.
    Simplified — no table selection, no floor plan.
    """
    try:
        order = Order.objects.get(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet,
        )
    except Order.DoesNotExist:
        raise Http404("Order not found")

    token = getattr(order, "token", None)

    # Only load available items — avoid showing 86'd items to cashier.
    categories = (
        MenuCategory.objects
        .filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            is_active=True,
        )
        .prefetch_related(
            Prefetch(
                "items",
                queryset=MenuItem.objects.filter(is_available=True),
            )
        )
    )

    config, _ = PaymentConfig.objects.get_or_create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
    )

    total_paid = (
        order.payments
        .exclude(method="refund")
        .aggregate(total=Sum("amount"))["total"] or Decimal("0")
    )
    remaining = max(Decimal("0"), order.grand_total - total_paid)

    return render(request, "orders/token_billing.html", {
        "order":      order,
        "token":      token,
        "categories": categories,
        "config":     config,
        "remaining":  remaining,
        "total_paid": total_paid,
    })
