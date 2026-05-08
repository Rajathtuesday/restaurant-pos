# reports/services/dashboard_metrics.py
from django.core.cache import cache
from django.db.models import Sum, Count, F
from django.utils.timezone import localdate

from orders.models import Order, Payment, Table
from inventory.models import InventoryItem
from tenants.models import Outlet


def owner_dashboard_metrics(user):
    tenant = user.tenant
    today = localdate()

    cache_key = f"dashboard_metrics_{tenant.id}_{user.outlet_id}_{today}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    if user.role == "owner":
        outlets = list(Outlet.objects.filter(tenant=tenant))
    else:
        outlets = list(Outlet.objects.filter(id=user.outlet.id))

    # --- Single aggregated query per metric using outlet__in ----------------
    outlet_ids = [o.id for o in outlets]

    revenue_qs = (
        Payment.objects
        .filter(order__tenant=tenant, order__outlet_id__in=outlet_ids, paid_at__date=today)
        .exclude(method="refund")
        .values("order__outlet_id")
        .annotate(total=Sum("amount"))
    )
    revenue_map = {r["order__outlet_id"]: r["total"] or 0 for r in revenue_qs}

    orders_qs = (
        Order.objects
        .filter(tenant=tenant, outlet_id__in=outlet_ids, created_at__date=today, status__in=["closed", "paid"])
        .values("outlet_id")
        .annotate(count=Count("id"))
    )
    orders_map = {o["outlet_id"]: o["count"] for o in orders_qs}

    active_tables_qs = (
        Table.objects
        .filter(tenant=tenant, outlet_id__in=outlet_ids, state__in=["ordering", "preparing", "ready"])
        .values("outlet_id")
        .annotate(count=Count("id"))
    )
    tables_map = {t["outlet_id"]: t["count"] for t in active_tables_qs}

    low_stock_qs = (
        InventoryItem.objects
        .filter(tenant=tenant, outlet_id__in=outlet_ids, stock__lte=F("low_stock_threshold"))
        .values("outlet_id")
        .annotate(count=Count("id"))
    )
    stock_map = {s["outlet_id"]: s["count"] for s in low_stock_qs}

    # --- Assemble results ---------------------------------------------------
    results = []
    for outlet in outlets:
        rev = revenue_map.get(outlet.id, 0)
        orders = orders_map.get(outlet.id, 0)
        results.append({
            "outlet": outlet.name,
            "revenue": rev,
            "orders": orders,
            "avg_order_value": (rev / orders) if orders else 0,
            "active_tables": tables_map.get(outlet.id, 0),
            "low_stock": stock_map.get(outlet.id, 0),
        })

    cache.set(cache_key, results, 60)  # 60-second TTL
    return results