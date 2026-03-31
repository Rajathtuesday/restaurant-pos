# reports/services/waiter_reports.py

from django.db.models import Count
from django.utils import timezone
from orders.models import Order


def waiter_performance(tenant, outlet=None, start_date=None, end_date=None):
    """
    Returns number of completed orders per waiter for today.
    - Uses only financially valid orders (paid/closed)
    - Supports multi-outlet (outlet=None)
    """

    if not start_date: start_date = timezone.now().date()
    if not end_date: end_date = timezone.now().date()

    # ----------------------------
    # BASE QUERY
    # ----------------------------
    query = Order.objects.filter(
        tenant=tenant,
        status__in=["paid", "closed"],
        created_at__date__gte=start_date, created_at__date__lte=end_date
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