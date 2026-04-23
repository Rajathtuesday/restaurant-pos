# reports/services/table_reports.py
from django.db.models import Count
from orders.models import Order, OrderItem
from django.utils import timezone


def table_turnover(tenant, outlet=None, start_date=None, end_date=None):
    query = Order.objects.filter(
        tenant=tenant,
        status__in=["closed", "paid"],
        table__isnull=False,
        created_at__date__gte=start_date if start_date else timezone.localdate(), created_at__date__lte=end_date if end_date else timezone.localdate()
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