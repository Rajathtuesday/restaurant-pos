# reports/services/audit_reports.py
"""
Discount / void staff audit -- who is discounting, comping, and voiding,
how often, and (for voids) why. Built from orders.models.OrderEvent, the
real audit trail, rather than reports.services.table_reports.void_items()
(confirmed dead code: not imported by any view, no staff attribution, and a
signature that doesn't match every sibling report service's
(tenant, outlet=None, start_date=None, end_date=None) convention).

Order-level discounts are unioned across two event shapes: pre- and
post-2026-07-26 (when apply_discount started using its own event_type
instead of the generic "status_changed"). Item-level discounts and
complimentary marks have no old shape to union -- they created no OrderEvent
at all before that same date, so this report is honest that data for those
two categories is only available from then onward, the same way
pl_reports.py is upfront about recipe coverage rather than pretending COGS
is complete.
"""
from django.db.models import Count, Q

from core.utils import get_business_date_range
from orders.models import OrderEvent


def discount_void_audit(tenant, outlet=None, start_date=None, end_date=None):
    if not start_date or not end_date:
        return {
            "discounts": [], "item_discounts": [], "comps": [], "voids": [],
            "void_reasons": [],
        }

    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    base_qs = OrderEvent.objects.filter(
        tenant=tenant, created_at__gte=range_start, created_at__lt=range_end,
    )
    if outlet:
        base_qs = base_qs.filter(outlet=outlet)

    def _by_staff(qs):
        return list(
            qs.values("created_by__username")
            .annotate(count=Count("id"))
            .order_by("-count")
        )

    # Order-level discounts: union the pre- and post-fix event shapes so
    # history isn't lost, but count is the metric (not a summed rupee value
    # -- metadata["value"] can be either a percentage or a flat amount
    # depending on metadata["type"], and the two aren't addable).
    discounts_qs = base_qs.filter(
        Q(event_type="discount_applied")
        | Q(event_type="status_changed", metadata__action="discount_applied")
    )
    discounts = _by_staff(discounts_qs)

    # Item-level discounts and comps -- new event types only, no pre-fix data.
    item_discounts = _by_staff(base_qs.filter(event_type="item_discount_applied"))
    comps = _by_staff(base_qs.filter(event_type="item_complimentary"))

    # Voids -- full history (item_voided has always been a dedicated,
    # reliable event type), staff attribution plus a reason breakdown.
    void_qs = base_qs.filter(event_type="item_voided")
    voids = _by_staff(void_qs)
    void_reasons = list(
        void_qs.values("metadata__reason")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    return {
        "discounts": discounts,
        "item_discounts": item_discounts,
        "comps": comps,
        "voids": voids,
        "void_reasons": void_reasons,
    }
