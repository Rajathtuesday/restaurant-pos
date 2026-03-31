# reports/services/item_reports.py

from django.db.models import Sum
from orders.models import OrderItem
from django.utils import timezone

def top_items(tenant, outlet=None, start_date=None, end_date=None):

    query = OrderItem.objects.filter(
        order__tenant=tenant,
        order__status__in=["paid","closed"],
        order__created_at__date__gte=start_date if start_date else timezone.now().date(), order__created_at__date__lte=end_date if end_date else timezone.now().date(),
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