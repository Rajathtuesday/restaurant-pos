from django.db.models import Sum, F
from orders.models import OrderItem


def category_sales(tenant, outlet):

    data = (
        OrderItem.objects
        .filter(
            order__tenant=tenant,
            order__outlet=outlet,
            order__status__in=["paid","closed"]
        )
        .values("menu_item__category__name")
        .annotate(
            revenue=Sum(F("price") * F("quantity"))
        )
        .order_by("-revenue")
    )

    return list(data)