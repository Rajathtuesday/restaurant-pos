# reports/services/kitchen_reports.py

import logging
from django.utils import timezone
from django.db.models import Sum, Count, Q
from orders.models import OrderItem, KOTBatch
from core.utils import get_business_date, get_business_date_range

logger = logging.getLogger("pos.reports")

def kitchen_performance(tenant, outlet=None, start_date=None, end_date=None):
    logger.debug("Fetching kitchen_performance for %s | Outlet: %s | %s to %s", tenant, outlet, start_date, end_date)
    if not start_date:
        start_date = get_business_date(timezone.now(), outlet)
    if not end_date:
        end_date = get_business_date(timezone.now(), outlet)
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    # Filter items that went to the kitchen (part of a KOT)
    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__gte=range_start,
        order__created_at__lt=range_end,
        kot__isnull=False
    )

    if outlet:
        items = items.filter(order__outlet=outlet)

    # General KOT statistics
    total_kots = KOTBatch.objects.filter(
        tenant=tenant,
        created_at__gte=range_start,
        created_at__lt=range_end
    )
    if outlet:
        total_kots = total_kots.filter(outlet=outlet)
        
    num_kots = total_kots.count()

    total_items_prepared = items.aggregate(total_qty=Sum('quantity'))['total_qty'] or 0
    total_voided = items.filter(status='voided').aggregate(total_qty=Sum('quantity'))['total_qty'] or 0

    return {
        "total_items_prepared": total_items_prepared,
        "total_kots": num_kots,
        "total_voided": total_voided,
    }

def top_kitchen_items(tenant, outlet=None, start_date=None, end_date=None):
    logger.debug("Fetching top_kitchen_items for %s | Outlet: %s | %s to %s", tenant, outlet, start_date, end_date)
    if not start_date: start_date = get_business_date(timezone.now(), outlet)
    if not end_date: end_date = get_business_date(timezone.now(), outlet)
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__gte=range_start,
        order__created_at__lt=range_end,
        kot__isnull=False
    ).exclude(status='voided')

    if outlet:
        items = items.filter(order__outlet=outlet)

    top = items.values('menu_item__name').annotate(
        total_qty=Sum('quantity')
    ).order_by('-total_qty')[:10]

    return list(top)
