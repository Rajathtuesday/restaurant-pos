# reports/services/item_reports.py

import logging
from django.db.models import Sum
from orders.models import OrderItem
from django.utils import timezone

logger = logging.getLogger("pos.reports")

def top_items(tenant, outlet=None, start_date=None, end_date=None):
    logger.debug(f"Fetching top_items for {tenant} | Outlet: {outlet} | {start_date} to {end_date}")


    query = OrderItem.objects.filter(
        order__tenant=tenant,
        order__status__in=["paid","closed"],
        order__created_at__date__gte=start_date if start_date else timezone.localdate(), order__created_at__date__lte=end_date if end_date else timezone.localdate(),
        is_complimentary=False,
    )

    if outlet:
        query = query.filter(order__outlet=outlet)

    items = (
        query
        .values("menu_item__name")
        .annotate(total=Sum("quantity"))
        .order_by("-total")[:10]
    )

    return items