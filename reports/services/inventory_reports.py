# reports/services/inventory_reports.py
import logging
from django.db.models import Sum
from inventory.models import InventoryTransaction
from core.utils import get_business_date_range

logger = logging.getLogger("pos.reports")


def _business_range_filter(qs, outlet, start_date, end_date):
    """
    Applies business-day-aware date bounds to an InventoryTransaction
    queryset. A plain created_at__date__gte/lte comparison would silently
    drop a transaction recorded after midnight but before the outlet's
    cutoff hour — e.g. a 2 AM wastage entry belongs to the previous
    business day, not the new calendar day.
    """
    if start_date:
        range_start, _ = get_business_date_range(start_date, outlet)
        qs = qs.filter(created_at__gte=range_start)
    if end_date:
        _, range_end = get_business_date_range(end_date, outlet)
        qs = qs.filter(created_at__lt=range_end)
    return qs


def _amount_used(rows):
    """
    Turn signed stock movements into amounts used, for display.

    The ledger stores stock going out as negative ("consume" -0.5 kg) and
    stock coming back as positive, which is right for a ledger but made the
    report show "Rs -1,234.00" of consumption, and sorted the least-used
    item first. Flip the sign once here, drop items that net to zero (a dish
    cancelled and put back the same day), and put the biggest first.
    """
    from decimal import Decimal as D
    out = []
    for row in rows:
        row["total_qty"] = -(row["total_qty"] or D("0"))
        if row["total_qty"]:
            out.append(row)
    return sorted(out, key=lambda r: r["total_qty"], reverse=True)


def inventory_usage(tenant, outlet, start_date=None, end_date=None):
    """How much of each item was used (a positive amount), most used first."""
    qs = InventoryTransaction.objects.filter(
        tenant=tenant,
        outlet=outlet,
        transaction_type="consume",
    )
    qs = _business_range_filter(qs, outlet, start_date, end_date)
    return _amount_used(list(
        qs.values("item__name", "item__unit")
          .annotate(total_qty=Sum("quantity"))
    ))


WASTAGE_SOURCES = ("all", "cancelled", "manual")


def _wastage_qs(tenant, outlet, start_date, end_date, source):
    qs = InventoryTransaction.objects.filter(
        tenant=tenant,
        outlet=outlet,
        transaction_type="wastage",
    )
    if source == "cancelled":
        qs = qs.filter(order_item__isnull=False)
    elif source == "manual":
        qs = qs.filter(order_item__isnull=True)
    return _business_range_filter(qs, outlet, start_date, end_date)


def inventory_wastage(tenant, outlet, start_date=None, end_date=None, source="all"):
    """
    How much of each item was wasted (a positive amount), with its cost at
    cost price, most wasted first.
    source: "all", "cancelled" (dishes cancelled after the kitchen made them)
    or "manual" (spillage, spoilage and other wastage logged by hand).
    """
    from decimal import Decimal as D
    rows = _amount_used(list(
        _wastage_qs(tenant, outlet, start_date, end_date, source)
        .values("item__name", "item__unit", "item__cost_price")
        .annotate(total_qty=Sum("quantity"))
    ))
    for row in rows:
        row["total_cost"] = row["total_qty"] * (row.get("item__cost_price") or D("0"))
    return rows


def cancelled_dish_wastage(tenant, outlet, start_date=None, end_date=None):
    """
    One row per cancelled dish that turned into wastage: when, which order,
    what, why, who, and the ingredient cost lost. Built from the wastage
    records' own link to the cancelled line, so it only ever shows this
    outlet's records.
    """
    from decimal import Decimal as D
    txns = (
        _wastage_qs(tenant, outlet, start_date, end_date, "cancelled")
        .select_related(
            "item", "order_item__menu_item", "order_item__voided_by",
            "order_item__order__table", "order_item__order__token",
        )
        .order_by("-created_at")
    )
    rows = {}
    for t in txns:
        oi = t.order_item
        row = rows.get(oi.id)
        if row is None:
            order = oi.order
            token = getattr(order, "token", None)
            where = order.table.name if order.table_id else (f"Token {token.display_number}" if token else order.get_source_display())
            staff = oi.voided_by
            row = rows[oi.id] = {
                "when": oi.voided_at or t.created_at,
                "where": where,
                "order_id": order.id,
                "dish": oi.menu_item.name if oi.menu_item else "Unknown",
                "quantity": oi.quantity,
                "reason": oi.void_reason or "",
                "staff": (staff.first_name or staff.username) if staff else "",
                "cost": D("0"),
            }
        row["cost"] += abs(t.quantity) * (t.item.cost_price or D("0"))
    return list(rows.values())


def inventory_cost(tenant, outlet, start_date=None, end_date=None):
    """What each item's usage cost at cost price (positive), dearest first."""
    from decimal import Decimal as D
    qs = InventoryTransaction.objects.filter(
        tenant=tenant,
        outlet=outlet,
        transaction_type="consume",
    )
    qs = _business_range_filter(qs, outlet, start_date, end_date)
    rows = _amount_used(list(
        qs.values("item__name", "item__unit", "item__cost_price")
          .annotate(total_qty=Sum("quantity"))
    ))
    for row in rows:
        row["total_cost"] = row["total_qty"] * (row.get("item__cost_price") or D("0"))
    return sorted(rows, key=lambda r: r["total_cost"], reverse=True)


def closing_stock(tenant, outlet):
    """Current stock snapshot — all items for the outlet, low-stock items first."""
    from inventory.models import InventoryItem
    return (
        InventoryItem.objects
        .filter(tenant=tenant, outlet=outlet)
        .order_by("stock")          # scarcest first
        .values("name", "stock", "unit", "low_stock_threshold", "cost_price")
    )


def production_capacity(tenant, outlet):
    """
    For each menu item that has recipes, calculate how many portions can be
    made from current stock. The limit is always the scarcest ingredient
    (min stock/qty_required across all recipe lines).
    """
    from menu.models import MenuItem
    from inventory.models import Recipe
    from inventory.unit_conversion import recipe_expected_quantity

    menu_items = (
        MenuItem.objects
        .filter(tenant=tenant, outlet=outlet, is_available=True)
        .prefetch_related("recipes__inventory_item", "category")
        .order_by("category__name", "name")
    )

    results = []
    for mi in menu_items:
        recipes = list(mi.recipes.all())
        if not recipes:
            continue

        lines = []
        for r in recipes:
            inv = r.inventory_item
            # A recipe's unit doesn't have to match the inventory item's own
            # unit (e.g. a recipe in grams against a kg-tracked item) — must
            # convert before dividing, same as KOT-time deduction does, or
            # "how many can I make" is off by whatever the unit ratio is.
            required_per = recipe_expected_quantity(
                r.quantity_required, r.unit, inv,
                logger=logger, context=f"Recipe {r.id} (menu item '{mi.name}')",
            )
            if required_per and required_per > 0:
                can_make = inv.stock / required_per
            else:
                # None (incompatible units) or zero/blank — treat as a hard
                # stop rather than silently excluding the line, since a
                # broken recipe line here means this dish's stock deduction
                # is unreliable, not just untracked for this report.
                can_make = 0
            lines.append({
                "ingredient": inv.name,
                "stock": inv.stock,
                "unit": inv.unit,
                "required_per": required_per if required_per is not None else r.quantity_required,
                "can_make": can_make,
                "is_low": inv.stock <= inv.low_stock_threshold,
            })

        if not lines:
            continue

        max_portions = int(min(l["can_make"] for l in lines))
        bottleneck = min(lines, key=lambda l: l["can_make"])

        results.append({
            "menu_item": mi.name,
            "category": mi.category.name,
            "max_portions": max_portions,
            "bottleneck": bottleneck["ingredient"],
            "bottleneck_stock": bottleneck["stock"],
            "bottleneck_unit": bottleneck["unit"],
            "lines": lines,
        })

    return sorted(results, key=lambda r: r["max_portions"])


def stock_ledger(tenant, outlet, start_date=None, end_date=None, item_id=None):
    """All transactions for the outlet, ordered by date desc."""
    qs = InventoryTransaction.objects.filter(
        tenant=tenant,
        outlet=outlet,
    ).select_related("item")
    qs = _business_range_filter(qs, outlet, start_date, end_date)
    if item_id:
        qs = qs.filter(item_id=item_id)
    return qs.order_by("-created_at")[:500]
