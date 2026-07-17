# reports/services/waiter_reports.py

from django.db.models import Count
from django.utils import timezone
from orders.models import Order
from core.utils import get_business_date, get_business_date_range


def waiter_performance(tenant, outlet=None, start_date=None, end_date=None):
    """
    Returns number of completed orders per waiter for today.
    - Uses only financially valid orders (paid/closed)
    - Supports multi-outlet (outlet=None)
    """

    if not start_date: start_date = get_business_date(timezone.now(), outlet)
    if not end_date: end_date = get_business_date(timezone.now(), outlet)
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    # ----------------------------
    # BASE QUERY — business-day bounds, not plain calendar dates
    # ----------------------------
    query = Order.objects.filter(
        tenant=tenant,
        status__in=["paid", "closed"],
        created_at__gte=range_start, created_at__lt=range_end
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