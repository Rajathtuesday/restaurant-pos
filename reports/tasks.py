# reports/tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger("pos.reports")


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    name="reports.tasks.send_daily_digest_email",
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_daily_digest_email(self):
    """
    Sends a compact digest (sales, net profit if advanced_reports is on,
    labor cost %) for the most recently completed business day to every
    active setup.ScheduledReportSubscription. Runs once a day via celery
    beat (core/settings.py CELERY_BEAT_SCHEDULE) -- see systemd/celery.service
    for why beat is embedded in the worker (-B) rather than a second service.

    v1 scope is deliberately this one compact digest, not every report from
    Phases 1-3 -- a full per-report-type schedule (weekly P&L, monthly CRM
    analytics) is a reasonable v2 once this is proven.
    """
    from core.features import has_feature
    from core.utils import get_business_date
    from reports.services.sales_reports import daily_sales
    from reports.services.pl_reports import net_profit_report
    from reports.services.labor_reports import labor_cost_report
    from setup.models import ScheduledReportSubscription

    sent_count = 0
    subs = ScheduledReportSubscription.objects.filter(is_active=True).select_related("tenant", "outlet")

    for sub in subs:
        recipients = sub.recipient_list
        if not recipients:
            continue

        tenant = sub.tenant
        outlet = sub.outlet
        # "Yesterday" (the most recently completed business day) -- this
        # runs early morning, before today's business day has any sales yet.
        business_date = get_business_date(timezone.now(), outlet) - timedelta(days=1)

        sales = daily_sales(tenant, outlet, business_date, business_date)
        lines = [
            f"Daily digest for {tenant.name}{' - ' + outlet.name if outlet else ' (all outlets)'}",
            f"Business date: {business_date}",
            "",
            f"Sales: Rs {sales.get('total_sales', 0):.2f} across {sales.get('orders', 0)} orders",
        ]

        if has_feature(tenant, "advanced_reports"):
            net_pl = net_profit_report(tenant, outlet, business_date, business_date)
            lines.append(
                f"Net profit: Rs {net_pl.get('net_profit', 0):.2f} ({net_pl.get('net_margin_pct', 0)}% margin)"
            )
            labor = labor_cost_report(tenant, outlet, business_date, business_date)
            lines.append(f"Labor cost: {labor.get('labor_cost_pct', 0)}% of revenue")

        try:
            send_mail(
                subject=f"Rasova daily digest - {tenant.name} ({business_date})",
                message="\n".join(lines),
                from_email=None,  # falls back to settings.DEFAULT_FROM_EMAIL
                recipient_list=recipients,
                fail_silently=False,
            )
            sent_count += 1
        except Exception:
            logger.exception("send_daily_digest_email failed for subscription %s", sub.id)

    return sent_count
