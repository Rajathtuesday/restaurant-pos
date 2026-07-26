# reports/tasks.py
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger("pos.reports")


def _email_is_configured():
    """
    Mirrors notifications/services/whatsapp_service.py's pattern: check real
    credentials exist before attempting a send, so a not-yet-configured
    channel fails closed (skip, log once) instead of repeatedly opening a
    real SMTP connection with blank credentials -- a pointless network round
    trip that just ties up a Celery worker slot once a day, forever, until
    someone happens to notice the logs. The moment EMAIL_USER/EMAIL_PASSWORD
    are set for real, this goes live with zero code changes, same promise
    the WhatsApp service makes for META_WHATSAPP_TOKEN/PHONE_ID.

    Console (dev) and locmem (tests) backends never touch the network, so
    there's nothing to gate for either -- only the real SMTP backend needs
    the credential check.
    """
    if settings.EMAIL_BACKEND != "django.core.mail.backends.smtp.EmailBackend":
        return True
    return bool(settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD)


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
    Sends a compact digest of everything built in the reporting gap-closing
    work for the most recently completed business day, to every active
    setup.ScheduledReportSubscription. Runs once a day via celery beat
    (core/settings.py CELERY_BEAT_SCHEDULE) -- see systemd/celery.service
    for why beat is embedded in the worker (-B) rather than a second service.

    Every section beyond the baseline sales line is feature-gated exactly
    like the report pages themselves are -- advanced_reports for the
    financial/operational sections, plus crm for the CRM section -- so a
    tenant without a feature enabled never sees a line implying they have it.
    """
    if not _email_is_configured():
        logger.info(
            "send_daily_digest_email: EMAIL_USER/EMAIL_PASSWORD not set (SMTP "
            "backend configured with no credentials) -- skipping entirely, "
            "no connection attempted. Set them in .env to go live."
        )
        return 0

    from core.features import has_feature
    from core.utils import get_business_date
    from reports.services.sales_reports import daily_sales
    from reports.services.pl_reports import net_profit_report
    from reports.services.labor_reports import labor_cost_report
    from reports.services.menu_engineering import menu_engineering_report
    from reports.services.audit_reports import discount_void_audit
    from reports.services.crm_reports import crm_analytics_report
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
            if labor.get("staff_with_unknown_cost"):
                lines.append(f"  ({labor['staff_with_unknown_cost']} staff missing a pay rate, excluded)")

            menu_eng = menu_engineering_report(tenant, outlet, business_date, business_date)
            quadrant_counts = {}
            for item in menu_eng["items"]:
                quadrant_counts[item["quadrant"]] = quadrant_counts.get(item["quadrant"], 0) + 1
            if quadrant_counts:
                summary = ", ".join(f"{v} {k}" for k, v in quadrant_counts.items())
                lines.append(f"Menu mix: {summary}")

            audit = discount_void_audit(tenant, outlet, business_date, business_date)
            total_discounts = sum(r["count"] for r in audit["discounts"]) + sum(r["count"] for r in audit["item_discounts"])
            total_voids = sum(r["count"] for r in audit["voids"])
            if total_discounts or total_voids:
                lines.append(f"Discounts: {total_discounts}, Voids: {total_voids} (see Discount/Void Audit for who)")

            if has_feature(tenant, "crm"):
                crm = crm_analytics_report(tenant, outlet, business_date, business_date)
                if crm["active_guests"]:
                    rating_str = f", avg rating {crm['avg_rating']}★" if crm["avg_rating"] is not None else ""
                    lines.append(f"Repeat customer rate: {crm['repeat_rate_pct']}%{rating_str}")

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
