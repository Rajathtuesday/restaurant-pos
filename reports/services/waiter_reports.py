# reports/services/waiter_reports.py

from django.db.models import Count
from django.utils import timezone
from orders.models import Order


def waiter_performance(tenant, outlet=None):
    """
    Returns number of completed orders per waiter for today.
    - Uses only financially valid orders (paid/closed)
    - Supports multi-outlet (outlet=None)
    """

    today = timezone.now().date()

    # ----------------------------
    # BASE QUERY
    # ----------------------------
    query = Order.objects.filter(
        tenant=tenant,
        status__in=["paid", "closed"],
        created_at__date=today
    )

    # ----------------------------
    # OPTIONAL OUTLET FILTER
    # ----------------------------
    if outlet:
        query = query.filter(outlet=outlet)

    # ----------------------------
    # AGGREGATION
    # ----------------------------
    data = (
        query
        .values("created_by__username")
        .annotate(orders=Count("id"))
        .order_by("-orders")
    )

    return list(data)