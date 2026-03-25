# reports/services/table_reports.py
from django.db.models import Count
from orders.models import Order, OrderItem
from django.utils import timezone


def table_turnover(tenant, outlet):
    query = Order.objects.filter(
        tenant=tenant,
        status__in=["closed", "paid"],
        table__isnull=False,
        created_at__date=timezone.now().date()
    )

    if outlet:
        query = query.filter(outlet=outlet)

    data = (
        query
        .values("table__name")
        .annotate(turnovers=Count("id"))
        .order_by("-turnovers")
    )

    return list(data)




def void_items(tenant, outlet):

    data = (
        OrderItem.objects
        .filter(
            order__tenant=tenant,
            order__outlet=outlet,
            status="voided"
        )
        .values(
            "menu_item__name",
            "void_reason"
        )
    )

    return list(data)