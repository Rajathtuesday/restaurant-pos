"""
Gross Margin / P&L report.

What we CAN calculate (data exists):
  Revenue            = sum of Order.grand_total (closed orders in period)
  GST collected      = sum of Order.gst_total
  Net revenue        = Revenue - GST
  Discounts given    = sum of Order.discount_total
  COGS               = sum over sold OrderItems of
                       (item.quantity × recipe.quantity_required × inventory_item.cost_price)
                       NOTE: only items WITH linked recipes contribute to COGS.
                       Items without recipes show COGS = 0 (unknown cost).
  Gross profit       = Net revenue - COGS
  Gross margin %     = (Gross profit / Net revenue) × 100

What we CANNOT calculate (data not tracked):
  Operating expenses (rent, salaries, utilities, marketing) — no model
  Net profit (would need operating expenses)
  → We call this "Revenue & Gross Margin" not "P&L" for accuracy.
"""
from decimal import Decimal

from django.db.models import Sum, F, ExpressionWrapper, DecimalField

from orders.models import Order, OrderItem
from inventory.models import Recipe


def gross_margin_report(tenant, outlet=None, start_date=None, end_date=None):
    """
    Returns revenue breakdown and gross margin for the given period.
    """
    if not start_date or not end_date:
        return _empty()

    order_qs = Order.objects.filter(
        tenant=tenant,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date,
        status__in=["closed", "paid"],
    )
    if outlet:
        order_qs = order_qs.filter(outlet=outlet)

    # ── Revenue totals ──────────────────────────────────────────
    totals = order_qs.aggregate(
        gross_revenue = Sum("grand_total"),
        gst_collected = Sum("gst_total"),
        discounts     = Sum("discount_total"),
        order_count   = Sum("id") - Sum("id") + Sum("id"),  # count
    )
    from django.db.models import Count
    totals2 = order_qs.aggregate(
        gross_revenue = Sum("grand_total"),
        gst_collected = Sum("gst_total"),
        discounts     = Sum("discount_total"),
        order_count   = Count("id"),
    )

    gross_revenue = float(totals2["gross_revenue"] or 0)
    gst_collected = float(totals2["gst_collected"] or 0)
    discounts     = float(totals2["discounts"]     or 0)
    order_count   = totals2["order_count"] or 0
    net_revenue   = gross_revenue - gst_collected

    # ── COGS from recipes ────────────────────────────────────────
    # OrderItems in closed orders in the period with a linked recipe
    item_qs = OrderItem.objects.filter(
        order__in=order_qs,
    ).exclude(status="voided").select_related("menu_item")

    cogs = Decimal("0")
    items_with_recipe = 0
    items_without_recipe = 0

    # Pre-fetch all recipes for efficiency
    recipe_map = {}
    for recipe in Recipe.objects.filter(
        menu_item__in=item_qs.values("menu_item_id")
    ).select_related("inventory_item"):
        recipe_map.setdefault(recipe.menu_item_id, []).append(recipe)

    for item in item_qs.iterator():
        recipes = recipe_map.get(item.menu_item_id, [])
        if recipes:
            items_with_recipe += 1
            for recipe in recipes:
                cost = recipe.inventory_item.cost_price
                qty  = item.quantity * recipe.quantity_required
                cogs += Decimal(str(cost)) * Decimal(str(qty))
        else:
            items_without_recipe += 1

    cogs_float    = float(cogs)
    gross_profit  = net_revenue - cogs_float
    gross_margin  = round((gross_profit / net_revenue * 100), 1) if net_revenue > 0 else 0

    # Coverage: % of items where we could calculate cost
    total_items = items_with_recipe + items_without_recipe
    recipe_coverage = round(items_with_recipe / total_items * 100, 1) if total_items else 0

    return {
        "gross_revenue":       round(gross_revenue, 2),
        "gst_collected":       round(gst_collected, 2),
        "net_revenue":         round(net_revenue, 2),
        "discounts":           round(discounts, 2),
        "cogs":                round(cogs_float, 2),
        "gross_profit":        round(gross_profit, 2),
        "gross_margin_pct":    gross_margin,
        "order_count":         order_count,
        "avg_order_value":     round(gross_revenue / order_count, 2) if order_count else 0,
        "recipe_coverage_pct": recipe_coverage,
        "items_with_recipe":   items_with_recipe,
        "items_without_recipe": items_without_recipe,
        # Note shown to the user
        "cogs_note": (
            f"COGS covers {recipe_coverage}% of items (those with recipes linked). "
            f"{items_without_recipe} items have no recipe — their cost is excluded."
            if items_without_recipe else
            "COGS covers 100% of items sold."
        ),
    }


def _empty():
    return {
        "gross_revenue": 0, "gst_collected": 0, "net_revenue": 0,
        "discounts": 0, "cogs": 0, "gross_profit": 0,
        "gross_margin_pct": 0, "order_count": 0, "avg_order_value": 0,
        "recipe_coverage_pct": 0, "items_with_recipe": 0,
        "items_without_recipe": 0, "cogs_note": "",
    }
