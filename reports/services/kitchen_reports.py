# reports/services/kitchen_reports.py

from django.utils import timezone
from django.db.models import Sum, Count, Q
from orders.models import OrderItem, KOTBatch

def kitchen_performance(tenant, outlet=None, start_date=None, end_date=None):
    if not start_date:
        start_date = timezone.now().date()
    if not end_date:
        end_date = timezone.now().date()

    # Filter items that went to the kitchen (part of a KOT)
    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__date__gte=start_date,
        order__created_at__date__lte=end_date,
        kot__isnull=False
    )
    
    if outlet:
        items = items.filter(order__outlet=outlet)

    # General KOT statistics
    total_kots = KOTBatch.objects.filter(
        tenant=tenant,
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
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
    if not start_date: start_date = timezone.now().date()
    if not end_date: end_date = timezone.now().date()

    items = OrderItem.objects.filter(
        order__tenant=tenant,
        order__created_at__date__gte=start_date,
        order__created_at__date__lte=end_date,
        kot__isnull=False
    ).exclude(status='voided')

    if outlet:
        items = items.filter(order__outlet=outlet)

    top = items.values('menu_item__name').annotate(
        total_qty=Sum('quantity')
    ).order_by('-total_qty')[:10]

    return list(top)
