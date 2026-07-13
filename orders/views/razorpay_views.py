# orders/views/razorpay_views.py
import json
import logging
from decimal import Decimal, InvalidOperation

import requests
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django_ratelimit.decorators import ratelimit

from core.decorators import tenant_required, feature_required
from core.features import has_feature
from orders.models import Order, OrderEvent, Payment, RazorpayQRCode
from orders.services.payment_service import process_payment
from orders.services.razorpay_gateway import create_qr_payment, verify_webhook_signature, paise_to_decimal
from setup.models import PaymentConfig
from tenants.models import Outlet

logger = logging.getLogger("pos.orders")


# -------------------------------------------------
# CREATE QR (cashier-facing)
# -------------------------------------------------

@login_required
@tenant_required
@feature_required("razorpay_gateway")
@require_POST
@ratelimit(key="user", rate="15/m", method="POST", block=True)
def create_razorpay_qr(request, order_id):
    order = get_object_or_404(
        Order, id=order_id, tenant=request.user.tenant, outlet=request.user.outlet
    )

    try:
        config = PaymentConfig.objects.get(tenant=request.user.tenant, outlet=request.user.outlet)
    except PaymentConfig.DoesNotExist:
        return JsonResponse({"error": "Payment methods not configured for this outlet."}, status=400)

    if not config.razorpay_enabled:
        return JsonResponse(
            {"error": "Razorpay is turned off for this outlet. Enable it in Setup → Payment Methods."},
            status=400,
        )
    if not config.razorpay_key_id or not config.razorpay_key_secret:
        # Tell the owner EXACTLY which credential is missing rather than a
        # blanket "not connected" — Razorpay hides the Key Secret behind a
        # separate "reveal" action in their dashboard, so it's a genuinely
        # easy field to miss when copying credentials in. A generic message
        # here just produces a confused "but I did connect it!" support ping.
        missing = []
        if not config.razorpay_key_id:
            missing.append("Key ID")
        if not config.razorpay_key_secret:
            missing.append("Key Secret")
        return JsonResponse(
            {"error": f"Razorpay is missing its {' and '.join(missing)}. "
                      f"Add it in Setup → Payment Methods, then try again."},
            status=400,
        )

    try:
        qr = create_qr_payment(order, config)
    except requests.RequestException as e:
        logger.error("Razorpay QR creation failed for order %s: %s", order_id, e)
        return JsonResponse({"error": "Could not reach Razorpay. Please try again."}, status=502)

    return JsonResponse({
        "qr_code_id": qr.qr_code_id,
        "image_url": qr.image_url,
        "expires_at": qr.expires_at.isoformat(),
    })


# -------------------------------------------------
# POLL QR STATUS (cashier-facing — bill screen polls this)
# -------------------------------------------------

@login_required
@tenant_required
@feature_required("razorpay_gateway")
@require_GET
def razorpay_qr_status(request, qr_code_id):
    qr = get_object_or_404(
        RazorpayQRCode, qr_code_id=qr_code_id,
        tenant=request.user.tenant, outlet=request.user.outlet,
    )
    return JsonResponse({"status": qr.status})


# -------------------------------------------------
# WEBHOOK (Razorpay-facing — no login, external caller)
# -------------------------------------------------

@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """
    Outlet is resolved from ?tenant_id=&outlet_id= — matches the existing
    aggregator webhook convention (orders/api.py::api_ingest_order takes
    both, not outlet_id alone). Signature can't be verified without first
    knowing which outlet's webhook secret to try.

    Requiring both (not just outlet_id, which is already globally unique
    and would resolve the tenant on its own) is a deliberate multi-tenant
    integrity check: the lookup below fails closed if a outlet_id is ever
    paired with the wrong tenant_id — e.g. a stale/copy-pasted webhook URL
    pointed at the wrong tenant's outlet — rather than silently succeeding
    because outlet_id alone happened to be enough.
    """
    tenant_id = request.GET.get("tenant_id")
    outlet_id = request.GET.get("outlet_id")
    if not tenant_id or not outlet_id:
        return JsonResponse({"error": "tenant_id and outlet_id required"}, status=400)

    outlet = get_object_or_404(Outlet, id=outlet_id, tenant_id=tenant_id)

    try:
        config = PaymentConfig.objects.get(tenant=outlet.tenant, outlet=outlet)
    except PaymentConfig.DoesNotExist:
        return JsonResponse({"error": "Razorpay not configured for this outlet"}, status=404)

    if not has_feature(outlet.tenant, "razorpay_gateway"):
        logger.warning("Razorpay webhook received for gated-off tenant %s — ignored", outlet.tenant_id)
        return JsonResponse({"error": "Feature not enabled"}, status=403)

    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_webhook_signature(request.body, signature, config.razorpay_webhook_secret):
        return JsonResponse({"error": "Invalid signature"}, status=401)

    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    event = payload.get("event", "")

    # QR closed/expired without payment — no-op, not an error. Razorpay
    # retries on non-2xx, so always return 200 for events we don't act on.
    if event in ("qr_code.closed",):
        qr_code_id = payload.get("payload", {}).get("qr_code", {}).get("entity", {}).get("id")
        # Scoped by tenant+outlet even though qr_code_id is already a Razorpay-
        # issued unique string — a valid signature only proves the caller knows
        # *this* outlet's webhook secret, not that qr_code_id belongs to it.
        RazorpayQRCode.objects.filter(
            qr_code_id=qr_code_id, tenant=outlet.tenant, outlet=outlet, status="active"
        ).update(status="expired")
        return JsonResponse({"ok": True})

    if event != "qr_code.credited":
        # Only QR-based payments are wired up — other events (direct payment
        # links, etc.) aren't part of this flow.
        return JsonResponse({"ok": True})

    # qr_code.credited payload shape: the QR entity carries the `notes` we set
    # at creation time; the payment entity carries the actual transaction id
    # and amount. Notes are NOT mirrored onto the payment entity.
    qr_entity = payload.get("payload", {}).get("qr_code", {}).get("entity", {}) or {}
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {}) or {}
    notes = qr_entity.get("notes", {}) or {}
    razorpay_payment_id = payment_entity.get("id")
    order_id = notes.get("order_id")
    webhook_amount_paise = payment_entity.get("amount")

    if not order_id or not razorpay_payment_id or webhook_amount_paise is None:
        logger.error("Razorpay webhook missing required fields: %s", payload)
        return JsonResponse({"error": "Malformed payload"}, status=400)

    # Idempotency — Razorpay may retry the same webhook.
    if Payment.objects.filter(reference=razorpay_payment_id).exists():
        return JsonResponse({"ok": True, "already_recorded": True})

    order = get_object_or_404(Order, id=order_id, tenant=outlet.tenant, outlet=outlet)

    # Integrity check: tenant+outlet in the URL must match tenant+outlet in
    # the QR's notes — catches a webhook routed to the right outlet but a
    # payload referencing a different tenant/outlet's order.
    if str(notes.get("outlet_id")) != str(outlet.id) or str(notes.get("tenant_id")) != str(outlet.tenant_id):
        logger.error(
            "Razorpay webhook tenant/outlet mismatch: URL tenant=%s outlet=%s, notes tenant=%s outlet=%s",
            outlet.tenant_id, outlet.id, notes.get("tenant_id"), notes.get("outlet_id"),
        )
        return JsonResponse({"error": "Tenant/outlet mismatch"}, status=400)

    webhook_amount = paise_to_decimal(int(webhook_amount_paise))

    # The .exists() check above closes the common case (retries seconds/minutes
    # apart), but is a plain SELECT-then-INSERT with no atomicity of its own —
    # two near-simultaneous deliveries could both pass it before either commits.
    # The real backstop is the database-level unique constraint on
    # Payment.reference: if it fires anywhere below, Postgres aborts this whole
    # atomic block (rolling back any partial OrderEvent/QR-status writes too),
    # and we treat it exactly like the idempotency check catching a duplicate.
    try:
        with transaction.atomic():
            paid_total = order.payments.exclude(method="refund").aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00")
            current_remaining = order.grand_total - paid_total

            # Validate against current state, not the QR's stale original quote —
            # the order may have changed (discount, added item) since the QR was shown.
            if current_remaining <= 0:
                # Reconciliation path: the order was already closed by another means
                # (e.g. cashier took cash) while this UPI payment was in flight.
                # The money is real and sitting in the restaurant's Razorpay account —
                # it must leave an audit trail, never silently disappear.
                Payment.objects.create(
                    order=order, method="upi", amount=webhook_amount, reference=razorpay_payment_id,
                )
                OrderEvent.objects.create(
                    tenant=order.tenant, outlet=order.outlet, order=order,
                    event_type="razorpay_overpaid_reconciliation",
                    amount=webhook_amount,
                    metadata={"razorpay_payment_id": razorpay_payment_id, "reason": "order already fully paid"},
                )
                logger.error(
                    "Razorpay payment %s for order #%s arrived after the order was already "
                    "fully paid by another method — recorded for manual reconciliation/refund.",
                    razorpay_payment_id, order.id,
                )
                RazorpayQRCode.objects.filter(
                    order=order, tenant=outlet.tenant, outlet=outlet, status="active"
                ).update(status="closed")
                return JsonResponse({"ok": True, "reconciliation_needed": True})

            amount_to_record = min(webhook_amount, current_remaining)
            if amount_to_record != webhook_amount:
                OrderEvent.objects.create(
                    tenant=order.tenant, outlet=order.outlet, order=order,
                    event_type="razorpay_amount_mismatch",
                    amount=webhook_amount,
                    metadata={
                        "razorpay_payment_id": razorpay_payment_id,
                        "webhook_amount": str(webhook_amount),
                        "recorded_amount": str(amount_to_record),
                    },
                )
                logger.warning(
                    "Razorpay webhook amount %s exceeds order #%s remaining %s — recorded %s, flagged.",
                    webhook_amount, order.id, current_remaining, amount_to_record,
                )

            try:
                result = process_payment(order, "upi", amount_to_record, user=None, reference=razorpay_payment_id)
                if result["order_closed"] and order.table:
                    # Mirrors pay_order's behaviour (payment_views.py) — process_payment
                    # itself doesn't know about tables, so every caller that closes an
                    # order is responsible for this transition.
                    order.table.state = "cleaning"
                    order.table.save(update_fields=["state"])
            except ValidationError:
                # Lost the race between this check and process_payment's own lock —
                # treat it the same as the already-fully-paid case above.
                Payment.objects.create(
                    order=order, method="upi", amount=amount_to_record, reference=razorpay_payment_id,
                )
                OrderEvent.objects.create(
                    tenant=order.tenant, outlet=order.outlet, order=order,
                    event_type="razorpay_overpaid_reconciliation",
                    amount=amount_to_record,
                    metadata={"razorpay_payment_id": razorpay_payment_id, "reason": "race with process_payment lock"},
                )
                logger.error(
                    "Razorpay payment %s for order #%s lost the payment-lock race — "
                    "recorded for manual reconciliation/refund.",
                    razorpay_payment_id, order.id,
                )

            RazorpayQRCode.objects.filter(
                order=order, tenant=outlet.tenant, outlet=outlet, status="active"
            ).update(status="paid")
    except IntegrityError:
        # The unique constraint on Payment.reference caught a genuine
        # simultaneous-delivery race the .exists() check above missed.
        return JsonResponse({"ok": True, "already_recorded": True})

    return JsonResponse({"ok": True})
