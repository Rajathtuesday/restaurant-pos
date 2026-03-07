# reports/services/sales_reports.py
from django.db.models import Sum
from django.utils import timezone
from django.db.models.functions import ExtractHour

from orders.models import Order, Payment


def daily_sales(tenant, outlet):

    today = timezone.now().date()

    orders = Order.objects.filter(
        tenant=tenant,
        outlet=outlet,
        created_at__date=today,
        status="closed"
    )

    total_sales = orders.aggregate(
        total=Sum("grand_total")
    )["total"] or 0

    total_orders = orders.count()

    payments = Payment.objects.filter(
        order__in=orders
    )

    payment_summary = payments.values("method").annotate(
        total=Sum("amount")
    )

    return {
        "total_sales": total_sales,
        "orders": total_orders,
        "payments": list(payment_summary)
    }


def hourly_sales(tenant, outlet):

    today = timezone.now().date()

    data = (
        Order.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            created_at__date=today,
            status="closed"
        )
        .annotate(hour=ExtractHour("created_at"))
        .values("hour")
        .annotate(total=Sum("grand_total"))
        .order_by("hour")
    )

    return list(data)