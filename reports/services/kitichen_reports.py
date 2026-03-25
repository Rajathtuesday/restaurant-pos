# reports/services/kitichen_reports.py
from django.db.models import Avg, F, ExpressionWrapper, DurationField
from orders.models import OrderEvent


def kitchen_times(tenant, outlet):

    data = (
        OrderEvent.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            event_type="kitchen_ready"
        )
        .values("order__items__menu_item__name")
        .annotate(
            avg_time=Avg("created_at")
        )
    )

    return list(data)