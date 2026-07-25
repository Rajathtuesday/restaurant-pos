"""
Gross Margin / P&L report.

gross_margin_report() -- what it calculates:
  Revenue            = sum of Payment rows for closed orders in period
  GST collected      = sum of Order.gst_total
  Net revenue        = Revenue - GST
  Discounts given    = sum of Order.discount_total
  COGS               = sum over sold OrderItems of
                       (item.quantity × recipe.quantity_required × inventory_item.cost_price),
                       computed per menu item by cogs.item_cogs_map().
                       NOTE: only items WITH linked recipes/modifiers contribute.
                       Items without either show COGS = 0 (unknown cost) --
                       see recipe_coverage_pct / cogs_note.
  Gross profit       = Net revenue - COGS
  Gross margin %     = (Gross profit / Net revenue) × 100

net_profit_report() -- gross_margin_report() plus finance.models.Expense for
the period, giving a real net profit number (Gross profit - operating
expenses). Gated behind the "advanced_reports" feature flag, since it needs
a tenant to actually be entering expenses for the number to mean anything.
"""
from decimal import Decimal

from django.db.models import Sum, Count

from orders.models import Order, OrderItem, Payment
from reports.services.cogs import item_cogs_map
import logging

logger = logging.getLogger("pos.reports")


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

    # Composition scheme outlets issue Bill of Supply — no GST to collect.
    # Zero out gst_total contribution from those outlets.
    non_comp_qs = order_qs.filter(outlet__is_composition_scheme=False)

    # ── Revenue totals ──────────────────────────────────────────
    # Gross revenue is summed from actual Payment rows for these orders, NOT
    # Order.grand_total. grand_total is the order's original billed amount and
    # never changes after a refund (approve_refund records the refund as a
    # separate negative Payment instead) — so summing grand_total overstated
    # revenue by the refunded amount and disagreed with the Sales Dashboard
    # (reports/services/sales_reports.py:daily_sales), which already nets
    # refunds this same way. Order scope (which orders count as "in period")
    # is unchanged — still order.created_at, not payment date.
    totals2 = order_qs.aggregate(
        discounts   = Sum("discount_total"),
        order_count = Count("id"),
    )
    revenue_agg = Payment.objects.filter(order__in=order_qs).aggregate(gross_revenue=Sum("amount"))
    # GST only from non-composition-scheme outlets
    gst_agg = non_comp_qs.aggregate(gst_collected=Sum("gst_total"))

    gross_revenue = float(revenue_agg["gross_revenue"] or 0)
    gst_collected = float(gst_agg["gst_collected"] or 0)
    discounts     = float(totals2["discounts"]     or 0)
    order_count   = totals2["order_count"] or 0
    net_revenue   = gross_revenue - gst_collected

    # ── COGS from recipes AND modifiers ────────────────────────────
    # OrderItems in closed orders in the period with a linked recipe
    item_qs = (
        OrderItem.objects.filter(order__in=order_qs)
        .exclude(status="voided")
    )

    # Per-item breakdown lives in cogs.py (shared with menu_engineering.py) —
    # this function only needs the total and the coverage counts.
    cogs_map, items_with_recipe, items_without_recipe = item_cogs_map(item_qs)
    cogs = sum(cogs_map.values(), Decimal("0"))

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


def net_profit_report(tenant, outlet=None, start_date=None, end_date=None):
    """
    gross_margin_report() plus operating expenses -- the piece that turns
    "gross margin" into an actual net profit number. Kept as a separate
    function (rather than folded into gross_margin_report itself) so every
    existing caller of gross_margin_report keeps its exact current output;
    this is additive, not a replacement.
    """
    from django.db.models import Q
    from finance.models import Expense

    gm = gross_margin_report(tenant, outlet, start_date, end_date)
    if not start_date or not end_date:
        return {**gm, "operating_expenses": 0, "net_profit": 0, "net_margin_pct": 0, "expense_breakdown": []}

    expense_qs = Expense.objects.filter(
        tenant=tenant, expense_date__gte=start_date, expense_date__lte=end_date,
    )
    if outlet:
        # A tenant-wide expense (outlet=None) counts against every outlet's
        # report, not just one -- it's real money spent regardless of which
        # outlet's numbers are being viewed.
        expense_qs = expense_qs.filter(Q(outlet=outlet) | Q(outlet__isnull=True))

    operating_expenses = expense_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    operating_expenses = float(operating_expenses)

    net_profit = gm["gross_profit"] - operating_expenses
    net_margin_pct = round(net_profit / gm["net_revenue"] * 100, 1) if gm["net_revenue"] > 0 else 0

    return {
        **gm,
        "operating_expenses": round(operating_expenses, 2),
        "net_profit": round(net_profit, 2),
        "net_margin_pct": net_margin_pct,
        "expense_breakdown": list(
            expense_qs.values("category").annotate(total=Sum("amount")).order_by("-total")
        ),
    }
