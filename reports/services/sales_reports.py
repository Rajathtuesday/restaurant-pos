# reports/services/sales_reports.py

from django.utils import timezone
from django.db.models import Sum
from django.db.models.functions import ExtractHour
from orders.models import Order, Payment


def daily_sales(tenant, outlet=None, start_date=None, end_date=None):
    """
    Daily financial report.

    IMPORTANT:
    - Revenue is ALWAYS derived from payments
    - Orders count is based on orders that actually have payments
    """

    if not start_date: start_date = timezone.localdate()
    if not end_date: end_date = timezone.localdate()

    # ----------------------------
    # PAYMENTS (SOURCE OF TRUTH)
    # ----------------------------
    payments = Payment.objects.filter(
        order__tenant=tenant,
        paid_at__date__gte=start_date, paid_at__date__lte=end_date
    )

    if outlet:
        payments = payments.filter(order__outlet=outlet)

    # ----------------------------
    # TOTAL SALES (net of refunds — negative Payment rows from approve_refund cancel out)
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
    # PAYMENT SPLIT (exclude refund rows — they affect total_sales, not a payment method)
    # ----------------------------
    from django.db.models import Sum as _Sum
    payment_split = (
        payments
        .exclude(method="refund")
        .values("method")
        .annotate(total=Sum("amount"))
    )

    # Surface refunds as a separate line so the report is transparent
    from django.db.models import Q as _Q
    net_refunds = abs(
        payments.filter(method="refund").aggregate(total=Sum("amount"))["total"] or 0
    )

    # ----------------------------
    # AVERAGE ORDER VALUE
    # ----------------------------
    avg_order = total_sales / total_orders if total_orders > 0 else 0

    return {
        "total_sales": float(total_sales),
        "orders": total_orders,
        "avg_order_value": float(avg_order),
        "payments": list(payment_split),
        "net_refunds": float(net_refunds),
    }


def hourly_sales(tenant, outlet=None, start_date=None, end_date=None):
    """
    Shows revenue distribution over time:
    - If 1 day: Groups by Hour
    - If multi-day: Groups by Date
    """
    from django.db.models.functions import TruncDate
    from datetime import timedelta

    if not start_date: start_date = timezone.localdate()
    if not end_date: end_date = timezone.localdate()

    payments = Payment.objects.filter(
        order__tenant=tenant,
        order__created_at__date__gte=start_date, order__created_at__date__lte=end_date
    )

    if outlet:
        payments = payments.filter(order__outlet=outlet)

    if start_date != end_date:
        payments = payments.annotate(date=TruncDate('order__created_at'))
        data = payments.values("date").annotate(total=Sum("amount")).order_by("date")
        
        days_diff = (end_date - start_date).days
        data_dict = {row["date"]: float(row["total"]) for row in data if row["date"]}
        result = []
        for i in range(days_diff + 1):
            curr_date = start_date + timedelta(days=i)
            result.append({
                "label": curr_date.strftime("%b %d"),
                "total": data_dict.get(curr_date, 0)
            })
        return result
    else:
        payments = payments.annotate(hour=ExtractHour("order__created_at"))
        data = payments.values("hour").annotate(total=Sum("amount"))
        
        hours = {h: 0 for h in range(24)}
        for row in data:
            if row["hour"] is not None:
                hours[row["hour"]] = float(row["total"])
                
        return [{"label": f"{h:02d}:00", "total": hours[h]} for h in range(24)]