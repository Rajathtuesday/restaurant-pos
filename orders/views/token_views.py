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
# WHY two separate token counters (DailyTokenCounter / DailyOnlineTokenCounter):
#   Counter tokens (#1 #2 …) and online tokens (O-1 O-2 …) must be
#   numbered independently.  A burst of Zomato orders must not push
#   walk-in numbers to #50 and confuse the kitchen.
#
# WHY Http404 in token_billing (not JsonResponse):
#   This is a page-render view.  Returning raw JSON on a page request
#   means Django's 404.html never renders and the user sees {"error":…}.
# ============================================================

import json
import logging
from decimal import Decimal

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required
from core.utils import get_business_date
from menu.models import MenuCategory, MenuItem
from orders.models import (
    Order, DailyTokenCounter, DailyOnlineTokenCounter, TokenOrder
)
from setup.models import PaymentConfig


def _popular_items(tenant, outlet, top_n=12):
    """
    Returns (popular_list, pop_counts) scoped strictly to tenant + outlet.
      popular_list — top_n MenuItem instances by 30-day order count
      pop_counts   — {item_id: count} used to sort items within categories
    Cached 15 min per outlet.
    """
    key = f"pop:{tenant.id}:{outlet.id}"
    result = cache.get(key)
    if result is None:
        cutoff = timezone.now() - timedelta(days=30)
        qs = (
            MenuItem.objects
            .filter(tenant=tenant, outlet=outlet, is_available=True)
            .annotate(
                order_count=Count(
                    "orderitem",
                    filter=Q(
                        orderitem__order__tenant=tenant,
                        orderitem__order__outlet=outlet,
                        orderitem__order__status__in=["closed", "paid"],
                        orderitem__order__created_at__gte=cutoff,
                    )
                )
            )
            .filter(order_count__gt=0)
            .order_by("-order_count")
        )
        popular_list = list(qs[:top_n])
        pop_counts   = {item.id: item.order_count for item in qs[:200]}
        result = (popular_list, pop_counts)
        cache.set(key, result, 900)
    return result

logger = logging.getLogger("pos.orders")

# ── role constants ──────────────────────────────────────────────────────────
_STAFF_CAN_CREATE = {"owner", "manager", "cashier"}
_STAFF_CAN_DISCOUNT = {"owner", "manager"}


# ------------------------------------------------------------------
# HELPER: fetch & split today's active tokens
# ------------------------------------------------------------------

def _get_active_tokens(outlet, today):
    """
    Returns (counter_tokens, online_tokens) QuerySets for today.
    Both are ordered by token_number.  Uses select_related to avoid
    N+1 on order.status / order.grand_total.
    """
    base = (
        TokenOrder.objects
        .filter(outlet=outlet, date=today, order__status__in=["open", "billing"])
        .select_related("order")
        .order_by("token_number")
    )
    counter_tokens = base.filter(is_online=False)
    online_tokens  = base.filter(is_online=True)
    return counter_tokens, online_tokens


# ------------------------------------------------------------------
# TOKEN DASHBOARD
# ------------------------------------------------------------------

@login_required
@tenant_required
@feature_required("token_system")
def token_dashboard(request):
    """
    Main screen for franchise and cafe ordering.
    Shows today's active tokens split into counter and online sections.
    Fine-dining tenants are blocked at decorator level.
    Role-aware: waiters see a read-only view (no New Order button).
    """
    outlet = request.user.outlet
    today  = get_business_date(timezone.now(), outlet)

    counter_tokens, online_tokens = _get_active_tokens(outlet, today)

    today_revenue = (
        Order.objects
        .filter(outlet=outlet, token__date=today, status__in=["closed", "paid"])
        .aggregate(total=Sum("grand_total"))["total"] or Decimal("0")
    )

    closed_tokens = TokenOrder.objects.filter(
        outlet=outlet, date=today, order__status__in=["closed", "paid"]
    ).count()

    cancelled_tokens = TokenOrder.objects.filter(
        outlet=outlet, date=today, order__status="cancelled"
    ).count()

    # Peek at counter for display only (no lock needed - approximate is fine)
    try:
        counter   = DailyTokenCounter.objects.get(outlet=outlet, date=today)
        next_token = counter.value + 1
    except DailyTokenCounter.DoesNotExist:
        from django.db.models import Max
        max_existing = TokenOrder.objects.filter(
            outlet=outlet, date=today, is_online=False
        ).aggregate(max_val=Max("token_number"))["max_val"]
        next_token = (max_existing or 0) + 1

    try:
        online_counter   = DailyOnlineTokenCounter.objects.get(outlet=outlet, date=today)
        next_online_token = online_counter.value + 1
    except DailyOnlineTokenCounter.DoesNotExist:
        from django.db.models import Max
        max_online = TokenOrder.objects.filter(
            outlet=outlet, date=today, is_online=True
        ).aggregate(max_val=Max("token_number"))["max_val"]
        next_online_token = (max_online or 0) + 1

    from setup.models import AggregatorConfig
    aggregator_config, _ = AggregatorConfig.for_outlet(outlet)

    can_create = request.user.role in _STAFF_CAN_CREATE or request.user.is_superuser

    return render(request, "orders/token_dashboard.html", {
        "counter_tokens":     counter_tokens,
        "online_tokens":      online_tokens,
        # keep for backward-compat with any template that checks active_tokens
        "active_tokens":      counter_tokens,
        "next_token":         next_token,
        "next_online_token":  next_online_token,
        "today_revenue":      today_revenue,
        "closed_tokens":      closed_tokens,
        "cancelled_tokens":   cancelled_tokens,
        "today":              today,
        "aggregator":         aggregator_config,
        "can_create":         can_create,
        "online_count":       online_tokens.count(),
    })


# ------------------------------------------------------------------
# DIRECT BILLING MODE  (creates token + immediately redirects)
# ------------------------------------------------------------------

@login_required
@tenant_required
@feature_required("token_system")
@require_POST
def create_and_go_to_billing(request):
    """
    One-tap shortcut for tenants with direct_billing_mode enabled.
    Creates a new counter token order and immediately redirects to its
    billing screen - skips the token dashboard entirely.
    Used by the owner dashboard "Core Ops" card when the feature flag is on.
    """
    if request.user.role not in _STAFF_CAN_CREATE and not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

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
            business_date = get_business_date(timezone.now(), outlet)

            counter, created = (
                DailyTokenCounter.objects
                .select_for_update()
                .get_or_create(
                    outlet=outlet, tenant=tenant,
                    date=business_date, defaults={"value": 0},
                )
            )
            if created:
                from django.db.models import Max
                max_existing = TokenOrder.objects.filter(
                    outlet=outlet, date=business_date, is_online=False
                ).aggregate(max_val=Max("token_number"))["max_val"]
                if max_existing:
                    counter.value = max_existing

            counter.value += 1
            counter.save(update_fields=["value"])
            next_token = counter.value

            order = Order.objects.create(
                tenant=tenant, outlet=outlet, table=None,
                created_by=request.user, status="open", source="counter",
                customer_name=customer_name, customer_phone=customer_phone,
            )

            TokenOrder.objects.create(
                tenant=tenant, outlet=outlet, order=order,
                token_number=next_token, date=business_date, is_online=False,
            )

        logger.info(
            "Direct-billing token #%s created | order=%s | outlet=%s | user=%s",
            next_token, order.id, outlet.id, request.user.username,
        )
        return JsonResponse({
            "success": True,
            "order_id": order.id,
            "token_number": next_token,
            "redirect": f"/token/{order.id}/bill/",
        })

    except Exception as e:
        logger.exception("Direct billing token creation failed: %s", e)
        return JsonResponse({"error": str(e)}, status=400)


# ------------------------------------------------------------------
# CREATE TOKEN ORDER  (POST only)
# ------------------------------------------------------------------

@login_required
@tenant_required
@feature_required("token_system")
@require_POST
def create_token_order(request):
    """
    Creates a new counter token order for franchise or cafe.
    Token number is assigned via DailyTokenCounter row-lock.
    Only counter staff, managers, and owners may create tokens.
    """
    if request.user.role not in _STAFF_CAN_CREATE and not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

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
            # 1. Business date
            business_date = get_business_date(timezone.now(), outlet)

            # 2. Lock counter row
            counter, created = (
                DailyTokenCounter.objects
                .select_for_update()
                .get_or_create(
                    outlet=outlet, tenant=tenant,
                    date=business_date, defaults={"value": 0},
                )
            )

            # SELF-HEAL: counter row was missing but tokens already exist
            if created:
                from django.db.models import Max
                max_existing = TokenOrder.objects.filter(
                    outlet=outlet, date=business_date, is_online=False
                ).aggregate(max_val=Max("token_number"))["max_val"]
                if max_existing:
                    counter.value = max_existing

            counter.value += 1
            counter.save(update_fields=["value"])
            next_token = counter.value

            # 3. Create Order
            order = Order.objects.create(
                tenant=tenant, outlet=outlet, table=None,
                created_by=request.user, status="open", source="counter",
                customer_name=customer_name, customer_phone=customer_phone,
            )

            # 4. Create TokenOrder with is_online=False (counter walk-in)
            TokenOrder.objects.create(
                tenant=tenant, outlet=outlet, order=order,
                token_number=next_token, date=business_date, is_online=False,
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
# ASSIGN ONLINE TOKEN  (called from api_ingest_order inside atomic)
# ------------------------------------------------------------------

def assign_online_token(order, outlet, tenant, business_date):
    """
    Creates a TokenOrder with is_online=True for an aggregator order.
    Must be called inside an existing transaction.atomic() block.

    Returns the created TokenOrder instance.
    """
    counter, created = (
        DailyOnlineTokenCounter.objects
        .select_for_update()
        .get_or_create(
            outlet=outlet, tenant=tenant,
            date=business_date, defaults={"value": 0},
        )
    )

    # SELF-HEAL: counter row missing but online tokens already exist
    if created:
        from django.db.models import Max
        max_existing = TokenOrder.objects.filter(
            outlet=outlet, date=business_date, is_online=True
        ).aggregate(max_val=Max("token_number"))["max_val"]
        if max_existing:
            counter.value = max_existing

    counter.value += 1
    counter.save(update_fields=["value"])

    return TokenOrder.objects.create(
        tenant=tenant, outlet=outlet, order=order,
        token_number=counter.value, date=business_date, is_online=True,
    )


# ------------------------------------------------------------------
# TOKEN BILLING SCREEN
# ------------------------------------------------------------------

@login_required
@tenant_required
@feature_required("token_system")
def token_billing(request, order_id):
    """
    Billing screen for a token order.
    Simplified - no table selection, no floor plan.
    Includes active_tokens context for the sidebar so staff can
    quickly switch between open orders without going back to dashboard.
    """
    outlet = request.user.outlet
    today  = get_business_date(timezone.now(), outlet)

    try:
        order = Order.objects.get(
            id=order_id,
            tenant=request.user.tenant,
            outlet=outlet,
        )
    except Order.DoesNotExist:
        raise Http404("Order not found")

    token = getattr(order, "token", None)

    # Popular items (tenant + outlet scoped, 15-min cache)
    popular_items, pop_counts = _popular_items(request.user.tenant, outlet)

    # Categories with items sorted by popularity within each section
    categories = list(
        MenuCategory.objects
        .filter(tenant=request.user.tenant, outlet=outlet, is_active=True)
        .prefetch_related(
            Prefetch(
                "items",
                queryset=MenuItem.objects.filter(is_available=True),
            )
        )
    )
    for cat in categories:
        cat._sorted_items = sorted(
            cat.items.all(),
            key=lambda i: pop_counts.get(i.id, 0),
            reverse=True,
        )

    config, _ = PaymentConfig.for_outlet(outlet, request.user.tenant)

    total_paid = (
        order.payments
        .exclude(method="refund")
        .aggregate(total=Sum("amount"))["total"] or Decimal("0")
    )
    remaining = max(Decimal("0"), order.grand_total - total_paid)

    # ── Active orders sidebar ─────────────────────────────────────────
    # All today's open/billing tokens for this outlet so the cashier
    # can jump between orders without returning to the dashboard.
    counter_tokens, online_tokens = _get_active_tokens(outlet, today)
    all_active = list(counter_tokens) + list(online_tokens)

    # Role gates passed down to template
    can_discount = request.user.role in _STAFF_CAN_DISCOUNT or request.user.is_superuser
    can_bypass   = request.user.role == "owner" or request.user.is_superuser

    return render(request, "orders/token_billing.html", {
        "order":          order,
        "token":          token,
        "categories":     categories,
        "popular_items":  popular_items,
        "config":         config,
        "remaining":      remaining,
        "total_paid":     total_paid,
        # sidebar data
        "counter_tokens": counter_tokens,
        "online_tokens":  online_tokens,
        "all_active":     all_active,
        # role gates
        "can_discount":   can_discount,
        "can_bypass":     can_bypass,
    })
