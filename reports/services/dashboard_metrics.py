# reports/services/dashboard_metrics.py
from django.db.models import Sum, Count, F
from django.utils.timezone import localdate

from orders.models import Order
from inventory.models import InventoryItem
from tenants.models import Outlet
from orders.models import Table
from orders.models import Payment

def owner_dashboard_metrics(user):

    tenant = user.tenant
    today = localdate()

    if user.role == "owner":
        outlets = Outlet.objects.filter(tenant=tenant)
    else:
        outlets = Outlet.objects.filter(id=user.outlet.id)

    results = []

    for outlet in outlets:

        orders_today = Order.objects.filter(
            tenant=tenant,
            outlet=outlet,
            created_at__date=today,
            status__in=["closed", "paid"]
        )



        payments = Payment.objects.filter(
            order__tenant=tenant,
            order__outlet=outlet,
            order__created_at__date=today
        )

        revenue = payments.aggregate(
            total=Sum("amount")
        )["total"] or 0

        total_orders = orders_today.count()

        avg_order_value = 0
        if total_orders:
            avg_order_value = revenue / total_orders

        active_tables = Table.objects.filter(
            tenant=tenant,
            outlet=outlet,
            state__in=["ordering", "preparing", "ready"]
        ).count()

        low_stock = InventoryItem.objects.filter(
            tenant=tenant,
            outlet=outlet,
            stock__lte=F("low_stock_threshold")
        ).count()

        results.append({
            "outlet": outlet.name,
            "revenue": revenue,
            "orders": total_orders,
            "avg_order_value": avg_order_value,
            "active_tables": active_tables,
            "low_stock": low_stock
        })

    return results