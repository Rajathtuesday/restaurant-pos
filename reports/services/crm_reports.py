# reports/services/crm_reports.py
"""
CRM/loyalty analytics -- repeat-customer rate, loyalty point trend, and
guest feedback trend. Guest/LoyaltyTransaction data is captured; none of it
was ever aggregated into a report before this.

Schema note: Guest and LoyaltyTransaction have no outlet field (crm/models.py
-- the loyalty program is tenant-wide, not per-outlet, in this schema), so
repeat rate and the loyalty trend are always tenant-wide regardless of the
outlet filter passed in. GuestFeedback DOES have an outlet field and is
filtered accordingly. This is a real schema constraint, not an oversight --
noted here and in the report template rather than silently ignored.
"""
from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate

from core.utils import get_business_date_range
from crm.models import GuestFeedback, LoyaltyTransaction


def crm_analytics_report(tenant, outlet=None, start_date=None, end_date=None):
    if not start_date or not end_date:
        return {"repeat_rate_pct": 0, "repeat_guests": 0, "active_guests": 0,
                "loyalty_trend": [], "feedback_trend": [], "avg_rating": None}

    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    earn_qs = LoyaltyTransaction.objects.filter(
        guest__tenant=tenant, transaction_type="earn",
        created_at__gte=range_start, created_at__lt=range_end,
    )

    # Period-precise repeat rate: among guests with >=1 earn transaction in
    # THIS period, what fraction have >=2 -- doesn't count history from
    # before the report window (Guest.visit_count is a lifetime total and
    # would conflate "ever a repeat customer" with "repeated in this window").
    per_guest_counts = earn_qs.values("guest_id").annotate(n=Count("id"))
    active_guests = per_guest_counts.count()
    repeat_guests = sum(1 for row in per_guest_counts if row["n"] >= 2)
    repeat_rate_pct = round(repeat_guests / active_guests * 100, 1) if active_guests else 0

    # Loyalty earn/redeem trend, by day.
    loyalty_qs = LoyaltyTransaction.objects.filter(
        guest__tenant=tenant,
        created_at__gte=range_start, created_at__lt=range_end,
    )
    loyalty_trend = list(
        loyalty_qs.annotate(day=TruncDate("created_at"))
        .values("day", "transaction_type")
        .annotate(points=Sum("points"), count=Count("id"))
        .order_by("day")
    )

    # Feedback rating trend, by day -- outlet-scoped, GuestFeedback has one.
    feedback_qs = GuestFeedback.objects.filter(
        tenant=tenant, created_at__gte=range_start, created_at__lt=range_end,
    )
    if outlet:
        feedback_qs = feedback_qs.filter(outlet=outlet)
    feedback_trend = list(
        feedback_qs.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(avg_rating=Avg("rating"), count=Count("id"))
        .order_by("day")
    )
    overall = feedback_qs.aggregate(avg=Avg("rating"))["avg"]

    return {
        "repeat_rate_pct": repeat_rate_pct,
        "repeat_guests": repeat_guests,
        "active_guests": active_guests,
        "loyalty_trend": loyalty_trend,
        "feedback_trend": feedback_trend,
        "avg_rating": round(overall, 2) if overall is not None else None,
    }
