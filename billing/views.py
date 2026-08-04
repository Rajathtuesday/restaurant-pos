# billing/views.py
"""
Rasova's own subscription billing webhook -- confirms payment against a
SubscriptionInvoice's Razorpay Payment Link, under Rasova's OWN account.
Deliberately separate from payments/razorpay_views.py's webhook, which
handles tenant-owned QR payments from restaurant diners.

Idempotency follows the exact same defense-in-depth shape as
payments/razorpay_views.py's razorpay_webhook: a fast .filter().exists()
pre-check for the common case (retries seconds/minutes apart), with the
REAL backstop being the database's own IntegrityError on
SubscriptionInvoice.razorpay_payment_id's unique constraint, which is what
actually closes the race for two near-simultaneous deliveries -- this is
tonight's own webhook-idempotency lesson, applied here on purpose.
"""
import json
import logging

from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import SubscriptionInvoice
from .razorpay_gateway import verify_webhook_signature

logger = logging.getLogger("pos.billing")


@csrf_exempt
@require_POST
def billing_webhook(request):
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_webhook_signature(request.body, signature):
        return JsonResponse({"error": "Invalid signature"}, status=401)

    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    event = payload.get("event", "")
    if event != "payment_link.paid":
        # Not an event this webhook acts on -- always 200, Razorpay retries
        # on non-2xx and there's nothing wrong with an event we ignore.
        return JsonResponse({"ok": True})

    link_entity = payload.get("payload", {}).get("payment_link", {}).get("entity", {}) or {}
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {}) or {}
    notes = link_entity.get("notes", {}) or {}

    invoice_id = notes.get("invoice_id")
    razorpay_payment_id = payment_entity.get("id")

    if (
        notes.get("kind") != "rasova_subscription"
        or not invoice_id or not str(invoice_id).isdigit()
        or not razorpay_payment_id
    ):
        # str(invoice_id).isdigit() matters, not just truthiness -- id is an
        # AutoField, and Django's ORM raises an unhandled ValueError (500)
        # trying to cast a non-numeric string in the .get() lookup below.
        logger.error("Billing webhook missing/mismatched fields: %s", payload)
        return JsonResponse({"error": "Malformed payload"}, status=400)

    # Fast path -- catches the common case (retry seconds/minutes later)
    # without needing to hit the IntegrityError path at all.
    if SubscriptionInvoice.objects.filter(razorpay_payment_id=razorpay_payment_id).exists():
        return JsonResponse({"ok": True, "already_recorded": True})

    try:
        invoice = SubscriptionInvoice.objects.get(id=invoice_id, razorpay_payment_link_id=link_entity.get("id"))
    except SubscriptionInvoice.DoesNotExist:
        logger.error("Billing webhook: no matching invoice for id=%s link=%s", invoice_id, link_entity.get("id"))
        return JsonResponse({"error": "Invoice not found"}, status=404)

    try:
        with transaction.atomic():
            # The atomic() here matters, not just the try/except: without a
            # savepoint, catching an IntegrityError leaves the connection's
            # enclosing transaction in a broken/aborted state (Postgres
            # refuses further queries until rollback) wherever this webhook
            # ever runs inside an outer transaction -- e.g. Django's
            # TestCase, or if ATOMIC_REQUESTS is ever turned on. atomic()
            # rolls back only this savepoint, leaving everything else fine.
            invoice.razorpay_payment_id = razorpay_payment_id
            invoice.status = "paid"
            invoice.paid_at = timezone.now()
            invoice.save(update_fields=["razorpay_payment_id", "status", "paid_at"])
    except IntegrityError:
        # The unique constraint on razorpay_payment_id caught a genuine
        # simultaneous-delivery race the .exists() check above missed --
        # exactly the scenario the earlier lesson tonight walked through.
        return JsonResponse({"ok": True, "already_recorded": True})

    tenant = invoice.tenant
    tenant.subscription_end_date = invoice.period_end
    if tenant.subscription_status != "active":
        tenant.subscription_status = "active"
    tenant.save(update_fields=["subscription_end_date", "subscription_status"])

    logger.info("Subscription invoice %s paid for tenant %s.", invoice.id, tenant.id)
    return JsonResponse({"ok": True})
