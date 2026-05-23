# reports/services/inventory_reports.py
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
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
        qs.values("item__name", "item__unit", "item__cost_price")
          .annotate(
              total_qty=Sum("quantity"),
              total_cost=ExpressionWrapper(
                  Sum("quantity") * F("item__cost_price"),
                  output_field=DecimalField(max_digits=14, decimal_places=2),
              ),
          )
          .order_by("-total_cost")
    )


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
