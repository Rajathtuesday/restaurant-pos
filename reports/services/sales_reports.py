# reports/services/sales_reports.py
from django.db.models import Sum
from django.utils import timezone
from django.db.models.functions import ExtractHour

from orders.models import Order, Payment


from django.db.models import Sum
from django.utils import timezone
from orders.models import Order, Payment


def daily_sales(tenant, outlet):

    today = timezone.now().date()

    orders = Order.objects.filter(
        tenant=tenant,
        outlet=outlet,
        created_at__date=today,
        status__in=["paid", "closed"]
    )

    total_sales = orders.aggregate(
        total=Sum("grand_total")
    )["total"] or 0

    total_orders = orders.count()

    avg_order_value = 0
    if total_orders:
        avg_order_value = total_sales / total_orders

    payments = Payment.objects.filter(order__in=orders)

    payment_summary = payments.values("method").annotate(
        total=Sum("amount")
    )

    return {
        "total_sales": total_sales,
        "orders": total_orders,
        "avg_order_value": avg_order_value,
        "payments": list(payment_summary)
    }


from django.db.models.functions import ExtractHour


def hourly_sales(tenant, outlet):

    today = timezone.now().date()

    data = (
        Order.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            created_at__date=today,
            status__in=["paid","closed"]
        )
        .annotate(hour=ExtractHour("created_at"))
        .values("hour")
        .annotate(total=Sum("grand_total"))
    )

    hours = {h: 0 for h in range(24)}

    for row in data:
        hours[row["hour"]] = row["total"]

    return [{"hour": h, "total": hours[h]} for h in range(24)]