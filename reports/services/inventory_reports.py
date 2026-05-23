# reports/services/inventory_reports.py
from django.db.models import Sum
from inventory.models import InventoryTransaction


def inventory_usage(tenant, outlet, start_date=None, end_date=None):
    """Total quantity consumed per item, optionally filtered by date range."""
    qs = InventoryTransaction.objects.filter(
        tenant=tenant,
        outlet=outlet,
        transaction_type="consume",
    )
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)
    return list(
        qs.values("item__name", "item__unit")
          .annotate(total_qty=Sum("quantity"))
          .order_by("-total_qty")
    )


def inventory_wastage(tenant, outlet, start_date=None, end_date=None):
    """Total quantity wasted per item, optionally filtered by date range."""
    qs = InventoryTransaction.objects.filter(
        tenant=tenant,
        outlet=outlet,
        transaction_type="wastage",
    )
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)
    return list(
        qs.values("item__name", "item__unit")
          .annotate(total_qty=Sum("quantity"))
          .order_by("-total_qty")
    )


def inventory_cost(tenant, outlet, start_date=None, end_date=None):
    """Total consumption cost per item (quantity × item cost_price)."""
    from decimal import Decimal as D
    qs = InventoryTransaction.objects.filter(
        tenant=tenant,
        outlet=outlet,
        transaction_type="consume",
    )
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)
    rows = list(
        qs.values("item__name", "item__unit", "item__cost_price")
          .annotate(total_qty=Sum("quantity"))
    )
    for row in rows:
        cost = row.get("item__cost_price") or D("0")
        row["total_cost"] = (row["total_qty"] or D("0")) * cost
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
            if r.quantity_required and r.quantity_required > 0:
                can_make = inv.stock / r.quantity_required
            else:
                can_make = 0
            lines.append({
                "ingredient": inv.name,
                "stock": inv.stock,
                "unit": inv.unit,
                "required_per": r.quantity_required,
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
    if start_date:
        qs = qs.filter(created_at__date__gte=start_date)
    if end_date:
        qs = qs.filter(created_at__date__lte=end_date)
    if item_id:
        qs = qs.filter(item_id=item_id)
    return qs.order_by("-created_at")[:500]
