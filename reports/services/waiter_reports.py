from django.db.models import Count
from orders.models import Order


def waiter_performance(tenant, outlet):

    data = (
        Order.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            status__in=["paid","closed"]
        )
        .values("created_by__username")
        .annotate(
            orders=Count("id")
        )
        .order_by("-orders")
    )

    return list(data)