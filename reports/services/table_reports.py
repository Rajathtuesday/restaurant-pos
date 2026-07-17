# reports/services/table_reports.py
from django.db.models import Count
from orders.models import Order, OrderItem
from django.utils import timezone
from core.utils import get_business_date, get_business_date_range


def table_turnover(tenant, outlet=None, start_date=None, end_date=None):
    start_date = start_date or get_business_date(timezone.now(), outlet)
    end_date = end_date or get_business_date(timezone.now(), outlet)
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    query = Order.objects.filter(
        tenant=tenant,
        status__in=["closed", "paid"],
        table__isnull=False,
        created_at__gte=range_start, created_at__lt=range_end
    )

    if outlet:
        query = query.filter(outlet=outlet)

    orders = query.values("table__name", "created_at", "closed_at", "updated_at")
    table_stats = {}
    for o in orders:
        tname = o["table__name"]
        if tname not in table_stats:
            table_stats[tname] = {"turnovers": 0, "total_mins": 0}
        
        table_stats[tname]["turnovers"] += 1
        
        end_time = o["closed_at"] or o["updated_at"]
        if end_time and o["created_at"]:
            delta = (end_time - o["created_at"]).total_seconds() / 60.0
            table_stats[tname]["total_mins"] += max(0, delta)
            
    result = []
    for tname, stats in table_stats.items():
        turnovers = stats["turnovers"]
        avg_mins = stats["total_mins"] / turnovers if turnovers > 0 else 0
        result.append({
            "table__name": tname,
            "turnovers": turnovers,
            "avg_turn_mins": avg_mins
        })
        
    result.sort(key=lambda x: x["turnovers"], reverse=True)
    return result




def void_items(tenant, outlet):

    data = (
        OrderItem.objects
        .filter(
            order__tenant=tenant,
            order__outlet=outlet,
            status="voided"
        )
        .values(
            "menu_item__name",
            "void_reason"
        )
    )

    return list(data)