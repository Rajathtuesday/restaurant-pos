# reports/services/menu_engineering.py
"""
Menu engineering (stars / plowhorses / puzzles / dogs) report — crosses
item_reports.top_items()'s popularity axis with cogs.item_cogs_map()'s
profitability axis into the classic four-quadrant view, split on the median
of each axis (standard menu-engineering methodology, not fixed thresholds).
"""
import statistics
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from orders.models import OrderItem
from core.utils import get_business_date, get_business_date_range
from reports.services.cogs import item_cogs_map


def menu_engineering_report(tenant, outlet=None, start_date=None, end_date=None):
    start_date = start_date or get_business_date(timezone.now(), outlet)
    end_date = end_date or get_business_date(timezone.now(), outlet)
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    base_qs = OrderItem.objects.filter(
        order__tenant=tenant,
        order__status__in=["paid", "closed"],
        order__created_at__gte=range_start, order__created_at__lt=range_end,
        is_complimentary=False,
    ).exclude(status="voided")
    if outlet:
        base_qs = base_qs.filter(order__outlet=outlet)

    # Popularity/revenue per item -- same shape as item_reports.top_items(),
    # but grouped by menu_item_id (not name) and with no [:10] cap: the whole
    # point of a quadrant view is seeing the full menu.
    sales = (
        base_qs.values("menu_item_id", "menu_item__name")
        .annotate(qty=Sum("quantity"), revenue=Sum("total_price"))
    )

    # COGS per item -- item_cogs_map() needs the raw (non-aggregated)
    # queryset, since it walks each OrderItem's recipe/modifier links.
    cogs_map, _, _ = item_cogs_map(base_qs)

    items = []
    for row in sales:
        menu_item_id = row["menu_item_id"]
        qty = row["qty"] or 0
        revenue = float(row["revenue"] or 0)
        cogs_known = menu_item_id in cogs_map
        cogs = float(cogs_map.get(menu_item_id, Decimal("0")))
        margin_pct = round((revenue - cogs) / revenue * 100, 1) if cogs_known and revenue > 0 else None

        items.append({
            "menu_item_id": menu_item_id,
            "name": row["menu_item__name"],
            "qty": qty,
            "revenue": round(revenue, 2),
            "cogs": round(cogs, 2) if cogs_known else None,
            "cogs_known": cogs_known,
            "margin_pct": margin_pct,
            "quadrant": None,  # filled in below
        })

    # Median splits. Popularity uses every item sold (doesn't need cost data);
    # profitability only uses items with a known cost -- an unknown-cost item
    # would otherwise silently drag the margin median in whichever direction
    # its (wrong) implied 0 cost happened to push it.
    qty_values = [i["qty"] for i in items]
    margin_values = [i["margin_pct"] for i in items if i["cogs_known"]]

    median_qty = statistics.median(qty_values) if qty_values else 0
    median_margin = statistics.median(margin_values) if margin_values else None

    for i in items:
        if not i["cogs_known"] or median_margin is None:
            i["quadrant"] = "Unknown"
            continue
        popular = i["qty"] >= median_qty
        profitable = i["margin_pct"] >= median_margin
        if popular and profitable:
            i["quadrant"] = "Star"
        elif popular and not profitable:
            i["quadrant"] = "Plowhorse"
        elif not popular and profitable:
            i["quadrant"] = "Puzzle"
        else:
            i["quadrant"] = "Dog"

    items.sort(key=lambda i: i["qty"], reverse=True)

    return {
        "items": items,
        "median_qty": median_qty,
        "median_margin_pct": median_margin,
        "items_with_unknown_cost": sum(1 for i in items if not i["cogs_known"]),
    }
