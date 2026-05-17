"""
Week-over-week and period comparison reports.
Returns current period vs previous period for the same length of time.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, Count, Avg, Q

from orders.models import Order, Payment, OrderItem


def period_comparison(tenant, outlet=None, start_date=None, end_date=None):
    """
    Compare current period (start_date–end_date) against the previous
    period of equal length.

    Returns a dict with current, previous, and delta (% change) for
    revenue, orders, avg order value, and top-line breakdown.
    """
    if not start_date or not end_date:
        return {}

    span        = (end_date - start_date).days + 1
    prev_end    = start_date - timedelta(days=1)
    prev_start  = prev_end - timedelta(days=span - 1)

    def _fetch(s, e):
        qs = Order.objects.filter(
            tenant=tenant,
            created_at__date__gte=s,
            created_at__date__lte=e,
            status__in=["closed", "paid"],
        )
        if outlet:
            qs = qs.filter(outlet=outlet)

        totals = qs.aggregate(
            revenue   = Sum("grand_total"),
            orders    = Count("id"),
            subtotal  = Sum("subtotal"),
            gst       = Sum("gst_total"),
            discounts = Sum("discount_total"),
        )
        revenue = float(totals["revenue"] or 0)
        orders  = totals["orders"] or 0
        return {
            "revenue":   revenue,
            "orders":    orders,
            "avg_order": round(revenue / orders, 2) if orders else 0,
            "subtotal":  float(totals["subtotal"]  or 0),
            "gst":       float(totals["gst"]       or 0),
            "discounts": float(totals["discounts"] or 0),
        }

    def _delta(curr, prev):
        if prev == 0:
            return None   # can't calculate % change from 0
        return round(((curr - prev) / prev) * 100, 1)

    current  = _fetch(start_date, end_date)
    previous = _fetch(prev_start, prev_end)

    return {
        "current":  current,
        "previous": previous,
        "prev_start": prev_start,
        "prev_end":   prev_end,
        "deltas": {
            "revenue":   _delta(current["revenue"],   previous["revenue"]),
            "orders":    _delta(current["orders"],    previous["orders"]),
            "avg_order": _delta(current["avg_order"], previous["avg_order"]),
            "discounts": _delta(current["discounts"], previous["discounts"]),
        },
    }
