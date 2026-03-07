# reports/services/item_reports.py
from django.db.models import Sum
from orders.models import OrderItem


def top_items(tenant, outlet):

    items = (
        OrderItem.objects
        .filter(
            order__tenant=tenant,
            order__outlet=outlet,
            order__status="closed"
        )
        .values("menu_item__name")
        .annotate(total=Sum("quantity"))
        .order_by("-total")[:10]
    )

    return items