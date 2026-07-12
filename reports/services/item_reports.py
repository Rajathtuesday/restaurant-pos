# reports/services/item_reports.py

import logging
from django.db.models import Sum
from orders.models import OrderItem
from django.utils import timezone

logger = logging.getLogger("pos.reports")

def top_items(tenant, outlet=None, start_date=None, end_date=None):
    logger.debug("Fetching top_items for %s | Outlet: %s | %s to %s", tenant, outlet, start_date, end_date)


    query = OrderItem.objects.filter(
        order__tenant=tenant,
        order__status__in=["paid","closed"],
        order__created_at__date__gte=start_date if start_date else timezone.localdate(), order__created_at__date__lte=end_date if end_date else timezone.localdate(),
        is_complimentary=False,
    ).exclude(status="voided")   # voided items were never actually sold — matches Order.recalculate_totals

    if outlet:
        query = query.filter(order__outlet=outlet)

    items = (
        query
        .values("menu_item__name")
        # total = quantity sold; total_rev = revenue. The dashboard CSV reads
        # `total_rev` — without this annotation it defaulted to 0, so every
        # exported item row showed zero revenue and a zero average rate.
        .annotate(total=Sum("quantity"), total_rev=Sum("total_price"))
        .order_by("-total")[:10]
    )

    return items