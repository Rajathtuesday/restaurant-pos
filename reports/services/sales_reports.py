# reports/services/sales_reports.py

from django.utils import timezone
from django.db.models import Sum
from django.db.models.functions import ExtractHour
from orders.models import Order, Payment


def daily_sales(tenant, outlet=None):
    """
    Daily financial report.

    IMPORTANT:
    - Revenue is ALWAYS derived from payments
    - Orders count is based on orders that actually have payments
    """

    today = timezone.now().date()

    # ----------------------------
    # PAYMENTS (SOURCE OF TRUTH)
    # ----------------------------
    payments = Payment.objects.filter(
        order__tenant=tenant,
        order__created_at__date=today
    )

    if outlet:
        payments = payments.filter(order__outlet=outlet)

    # ----------------------------
    # TOTAL SALES
    # ----------------------------
    total_sales = payments.aggregate(
        total=Sum("amount")
    )["total"] or 0

    # ----------------------------
    # ORDERS (ONLY THOSE WITH PAYMENTS)
    # ----------------------------
    order_ids = payments.values_list("order_id", flat=True).distinct()

    orders = Order.objects.filter(id__in=order_ids)

    total_orders = orders.count()

    # ----------------------------
    # PAYMENT SPLIT
    # ----------------------------
    payment_split = (
        payments
        .values("method")
        .annotate(total=Sum("amount"))
    )

    # ----------------------------
    # AVERAGE ORDER VALUE
    # ----------------------------
    avg_order = total_sales / total_orders if total_orders > 0 else 0

    return {
        "total_sales": float(total_sales),
        "orders": total_orders,
        "avg_order_value": float(avg_order),
        "payments": list(payment_split)
    }


def hourly_sales(tenant, outlet=None):
    """
    Hourly revenue distribution (based on payments)
    """

    today = timezone.now().date()

    payments = Payment.objects.filter(
        order__tenant=tenant,
        order__created_at__date=today
    )

    if outlet:
        payments = payments.filter(order__outlet=outlet)

    payments = payments.annotate(
        hour=ExtractHour("order__created_at")
    )

    data = (
        payments
        .values("hour")
        .annotate(total=Sum("amount"))
    )

    # ----------------------------
    # FILL MISSING HOURS
    # ----------------------------
    hours = {h: 0 for h in range(24)}

    for row in data:
        hours[row["hour"]] = float(row["total"])

    return [{"hour": h, "total": hours[h]} for h in range(24)]