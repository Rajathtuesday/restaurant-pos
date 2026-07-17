# reports/services/category_reports.py
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from orders.models import OrderItem
from django.utils import timezone
from core.utils import get_business_date, get_business_date_range


def category_sales(tenant, outlet=None, start_date=None, end_date=None):

    # ---------------------------------------------
    # SAFE REVENUE EXPRESSION
    # ---------------------------------------------

    revenue_expr = ExpressionWrapper(
        F("price") * F("quantity"),
        output_field=DecimalField()
    )

    # ---------------------------------------------
    # QUERY — business-day bounds, not plain calendar dates
    # ---------------------------------------------

    start_date = start_date or get_business_date(timezone.now(), outlet)
    end_date = end_date or get_business_date(timezone.now(), outlet)
    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    query = OrderItem.objects.filter(
        order__tenant=tenant,
        order__status__in=["paid", "closed"],
        is_complimentary=False,
        order__created_at__gte=range_start, order__created_at__lt=range_end
    ).exclude(status="voided")

    if outlet:
        query = query.filter(order__outlet=outlet)

    data = (
        query
        .values("menu_item__category__name")
        .annotate(revenue=Sum("total_price"))  # 🔥 IMPORTANT FIX
        .order_by("-revenue")
    )

    return list(data)