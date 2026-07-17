# reports/services/dashboard_metrics.py
from django.core.cache import cache
from django.db.models import Sum, Count, F
from django.utils import timezone

from orders.models import Order, Payment, Table
from inventory.models import InventoryItem
from tenants.models import Outlet
from core.utils import get_business_date, get_business_date_range


def owner_dashboard_metrics(user):
    tenant = user.tenant

    if user.role == "owner":
        outlets = list(Outlet.objects.filter(tenant=tenant))
    else:
        outlets = list(Outlet.objects.filter(id=user.outlet.id))

    # Each outlet can have its own business_day_start_hour, so "today" isn't
    # one shared date across a multi-outlet tenant — outlet A might already
    # be on the next business day while outlet B (opened later, or on a
    # later cutoff) is still on the previous one. A single shared
    # `today = localdate()` used for every outlet, and a plain
    # created_at__date= filter, meant any order placed after midnight but
    # before an outlet's cutoff hour vanished from that outlet's own
    # dashboard until the calendar caught up — the exact bug already found
    # and fixed in the Z-report, live here on the page an owner actually
    # watches all day.
    business_dates = {o.id: get_business_date(timezone.now(), o) for o in outlets}
    ranges = {o.id: get_business_date_range(business_dates[o.id], o) for o in outlets}

    cache_key = f"dashboard_metrics_{tenant.id}_{user.outlet_id}_" + "_".join(
        f"{oid}:{d}" for oid, d in sorted(business_dates.items())
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Low stock and kitchen queue are point-in-time, not "today" scoped —
    # still fine to batch across outlets. Everything "today"-scoped is
    # computed per-outlet below since each has its own business-day window.
    outlet_ids = [o.id for o in outlets]

    active_tables_qs = (
        Table.objects
        .filter(tenant=tenant, outlet_id__in=outlet_ids, state__in=["ordering", "preparing", "ready"])
        .values("outlet_id")
        .annotate(count=Count("id"))
    )
    tables_map = {t["outlet_id"]: t["count"] for t in active_tables_qs}

    kitchen_qs = (
        Order.objects
        .filter(
            tenant=tenant,
            outlet_id__in=outlet_ids,
            status__in=["open", "billing"],
            items__status__in=["sent", "preparing"],
        )
        .values("outlet_id")
        .annotate(count=Count("id", distinct=True))
    )
    kitchen_map = {k["outlet_id"]: k["count"] for k in kitchen_qs}

    low_stock_qs = (
        InventoryItem.objects
        .filter(tenant=tenant, outlet_id__in=outlet_ids, stock__lte=F("low_stock_threshold"))
        .values("outlet_id")
        .annotate(count=Count("id"))
    )
    stock_map = {s["outlet_id"]: s["count"] for s in low_stock_qs}

    # Assemble — each outlet queried with its own business-day window.
    results = []
    for outlet in outlets:
        start, end = ranges[outlet.id]

        rev = (
            Payment.objects
            .filter(order__tenant=tenant, order__outlet_id=outlet.id, paid_at__gte=start, paid_at__lt=end)
            .exclude(method="refund")
            .aggregate(total=Sum("amount"))["total"] or 0
        )
        orders = Order.objects.filter(
            tenant=tenant, outlet_id=outlet.id, created_at__gte=start, created_at__lt=end,
            status__in=["closed", "paid"],
        ).count()
        voids = Order.objects.filter(
            tenant=tenant, outlet_id=outlet.id, created_at__gte=start, created_at__lt=end,
            status="cancelled",
        ).count()
        disc = Order.objects.filter(
            tenant=tenant, outlet_id=outlet.id, created_at__gte=start, created_at__lt=end,
            discount_total__gt=0,
        ).aggregate(total=Sum("discount_total"), count=Count("id"))

        results.append({
            "outlet":          outlet.name,
            "revenue":         rev,
            "orders":          orders,
            "avg_order_value": (rev / orders) if orders else 0,
            "active_tables":   tables_map.get(outlet.id, 0),
            "kitchen_orders":  kitchen_map.get(outlet.id, 0),
            "low_stock":       stock_map.get(outlet.id, 0),
            "voids_today":     voids,
            "discounts_amount": disc["total"] or 0,
            "discounts_count":  disc["count"] or 0,
        })

    cache.set(cache_key, results, 60)  # 60-second TTL
    return results