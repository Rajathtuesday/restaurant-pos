# reports/services/category_reports.py
from django.db.models import Sum, F, ExpressionWrapper, DecimalField
from orders.models import OrderItem
from django.utils import timezone


def category_sales(tenant, outlet=None, start_date=None, end_date=None):

    # ---------------------------------------------
    # SAFE REVENUE EXPRESSION
    # ---------------------------------------------

    revenue_expr = ExpressionWrapper(
        F("price") * F("quantity"),
        output_field=DecimalField()
    )

    # ---------------------------------------------
    # QUERY
    # ---------------------------------------------

    query = OrderItem.objects.filter(
        order__tenant=tenant,
        order__status__in=["paid", "closed"],
        is_complimentary=False,
        order__created_at__date__gte=start_date if start_date else timezone.now().date(), order__created_at__date__lte=end_date if end_date else timezone.now().date()
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