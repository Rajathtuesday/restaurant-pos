# reports/services/inventory_reports.py
from django.db.models import Sum
from inventory.models import InventoryTransaction


def inventory_usage(tenant, outlet):

    usage = (
        InventoryTransaction.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            type="usage"
        )
        .values("item__name")
        .annotate(total=Sum("quantity"))
        .order_by("-total")
    )

    return list(usage)


def inventory_cost(tenant, outlet):

    usage = (
        InventoryTransaction.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            type="usage"
        )
        .values("item__name")
        .annotate(total=Sum("quantity"))
        .order_by("-total")
    )

    return list(usage)