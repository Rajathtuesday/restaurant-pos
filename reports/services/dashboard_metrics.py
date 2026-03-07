from django.db.models import Sum, F , Count
from django.utils import timezone

from accounts import models
from orders.models import Order, Table
from inventory.models import InventoryItem
from tenants.models import Outlet


def owner_dashboard_metrics(user):

    tenant = user.tenant
    today = timezone.now().date()

    if user.role == "owner":
        outlets = Outlet.objects.filter(tenant=tenant)
    else:
        outlets = [user.outlet]

    metrics = []

    for outlet in outlets:

        orders = Order.objects.filter(
            tenant=tenant,
            outlet=outlet,
            created_at__date=today
        )

        revenue = orders.filter(
            status="closed"
        ).aggregate(total=Sum("grand_total"))["total"] or 0

        active_tables = Table.objects.filter(
            tenant=tenant,
            outlet=outlet,
            state__in=["ordering","preparing","ready","served"]
        ).count()

        kitchen_orders = orders.filter(
            status="open"
        ).count()

        low_stock = InventoryItem.objects.filter(
            tenant=tenant,
            outlet=outlet,
            stock__lte=F("low_stock_threshold")
        ).count()

        metrics.append({
            "outlet": outlet.name,
            "revenue": revenue,
            "orders": orders.count(),
            "active_tables": active_tables,
            "kitchen_orders": kitchen_orders,
            "low_stock": low_stock
        })
    
    return metrics