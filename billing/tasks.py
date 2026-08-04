# billing/tasks.py
"""
The two scheduled jobs behind Rasova's own subscription billing.
Both registered in CELERY_BEAT_SCHEDULE (core/settings.py).

Each per-tenant iteration is wrapped in try/except so one tenant's failure
(bad phone number, Razorpay API hiccup, bounced email) never blocks the
rest of the batch -- same "never raises" philosophy as
notifications/services/whatsapp_service.py.
"""
import logging
from datetime import timedelta
from dateutil.relativedelta import relativedelta

from celery import shared_task
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger("pos.billing")

OVERDUE_WARNING_DAYS = 5
OVERDUE_SUSPEND_DAYS = 14


@shared_task(name="billing.tasks.generate_monthly_invoices")
def generate_monthly_invoices():
    """
    For every tenant with subscription_status='active' whose current
    billing period has ended (or who has never been billed), create the
    next SubscriptionInvoice, a Razorpay Payment Link under Rasova's own
    account, the PDF, and send it by email + WhatsApp.

    Trial tenants and the documented first-month-free customer are
    skipped automatically -- they're not subscription_status='active' yet.
    """
    from tenants.models import Tenant
    from .models import SubscriptionInvoice
    from .razorpay_gateway import create_subscription_payment_link
    from .services import render_invoice_pdf
    from notifications.services.whatsapp_service import send_subscription_invoice

    today = timezone.now().date()
    created, failed = 0, 0

    for tenant in Tenant.objects.filter(subscription_status="active"):
        try:
            last_invoice = tenant.subscription_invoices.order_by("-period_end").first()
            period_start = last_invoice.period_end if last_invoice else (
                tenant.subscription_start_date or today
            )
            if period_start > today:
                continue  # not due yet
            period_end = period_start + relativedelta(months=1)

            from accounts.models import User
            owner = User.objects.filter(tenant=tenant, role="owner").first()

            with transaction.atomic():
                invoice, was_created = SubscriptionInvoice.objects.get_or_create(
                    tenant=tenant, period_start=period_start, period_end=period_end,
                    defaults={"amount": tenant.subscription_fee},
                )
                if not was_created:
                    continue  # already generated this period -- idempotent, matches the unique constraint

                # Payment-link creation happens INSIDE the same transaction
                # as the invoice row itself: if Razorpay's API call fails,
                # the invoice must not be left behind half-created (status=
                # 'pending', no link) -- that would silently and permanently
                # block this tenant from ever being billed for this period,
                # since the next run's get_or_create would just find the
                # same broken row and skip it. Rolling both back together
                # means a transient API failure gets retried cleanly on the
                # next scheduled run instead.
                link_id, link_url = create_subscription_payment_link(invoice, owner_user=owner)
                invoice.razorpay_payment_link_id = link_id
                invoice.razorpay_payment_link_url = link_url
                invoice.save(update_fields=["razorpay_payment_link_id", "razorpay_payment_link_url"])

            pdf_bytes = render_invoice_pdf(invoice, payment_link_url=link_url)
            if owner and owner.email:
                email = EmailMessage(
                    subject=f"Rasova subscription invoice — {tenant.name}",
                    body=f"Your Rasova invoice for {period_start} to {period_end} is attached.\n\nPay online: {link_url}",
                    to=[owner.email],
                )
                email.attach(f"rasova_invoice_{invoice.id}.pdf", pdf_bytes, "application/pdf")
                email.send(fail_silently=True)

            send_subscription_invoice(invoice, payment_link_url=link_url)

            created += 1
        except Exception as exc:
            failed += 1
            logger.error("generate_monthly_invoices failed for tenant %s: %s", tenant.id, exc)

    logger.info("generate_monthly_invoices: %s created, %s failed", created, failed)
    return {"created": created, "failed": failed}


@shared_task(name="billing.tasks.enforce_overdue_subscriptions")
def enforce_overdue_subscriptions():
    """
    Daily check that finally automates what's currently a 100% manual
    switch: a pending invoice past OVERDUE_WARNING_DAYS gets one warning
    (sent once, tracked via overdue_warning_sent_at so it never repeats);
    past OVERDUE_SUSPEND_DAYS, the tenant is suspended -- the exact
    mechanism SubscriptionStatusMiddleware already enforces, just finally
    driven by something real instead of a human remembering to check.
    """
    from .models import SubscriptionInvoice
    from notifications.services.whatsapp_service import send_subscription_invoice

    today = timezone.now().date()
    warned, suspended = 0, 0

    # Mark invoices actually overdue (status transition, not a new query concept)
    SubscriptionInvoice.objects.filter(
        status="pending", period_end__lt=today
    ).update(status="overdue")

    warning_cutoff = today - timedelta(days=OVERDUE_WARNING_DAYS)
    # select_related("tenant") -- send_subscription_invoice() and its message
    # builder both read invoice.tenant; without this every iteration below
    # was a separate query for the same relation (classic N+1).
    for invoice in SubscriptionInvoice.objects.filter(
        status="overdue", period_end__lte=warning_cutoff, overdue_warning_sent_at__isnull=True
    ).select_related("tenant"):
        try:
            send_subscription_invoice(invoice, payment_link_url=invoice.razorpay_payment_link_url)
            invoice.overdue_warning_sent_at = timezone.now()
            invoice.save(update_fields=["overdue_warning_sent_at"])
            warned += 1
        except Exception as exc:
            logger.error("Overdue warning failed for invoice %s: %s", invoice.id, exc)

    suspend_cutoff = today - timedelta(days=OVERDUE_SUSPEND_DAYS)
    overdue_tenant_ids = SubscriptionInvoice.objects.filter(
        status="overdue", period_end__lte=suspend_cutoff
    ).values_list("tenant_id", flat=True).distinct()

    from tenants.models import Tenant
    for tenant in Tenant.objects.filter(id__in=overdue_tenant_ids, subscription_status="active"):
        try:
            tenant.subscription_status = "suspended"
            tenant.save(update_fields=["subscription_status"])
            suspended += 1
            logger.warning("Tenant %s auto-suspended for overdue subscription.", tenant.id)
        except Exception as exc:
            logger.error("Auto-suspend failed for tenant %s: %s", tenant.id, exc)

    logger.info("enforce_overdue_subscriptions: %s warned, %s suspended", warned, suspended)
    return {"warned": warned, "suspended": suspended}
