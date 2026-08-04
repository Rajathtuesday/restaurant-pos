# billing/razorpay_gateway.py
"""
Rasova's OWN Razorpay integration — for charging restaurant tenants their
Rasova subscription fee. Deliberately separate from payments/razorpay_gateway.py,
which is the tenant-owned, bring-your-own-keys integration used to collect a
RESTAURANT's payments from ITS OWN diners. Never share credentials or code
paths between the two -- see md_files/eli5_subscription_billing_plan.html
for why that separation is load-bearing, not just tidiness.

Uses `requests` directly and manual HMAC verification, matching the same
house convention as payments/razorpay_gateway.py, for one reason: so anyone
reading both files recognizes the same shape immediately.

Honest caveat: the Payment Links request/response field names below match
Razorpay's documented API as of this writing, but this hasn't been tested
against a real Razorpay account from this session (no live credentials, no
internet access here) -- verify the exact response shape against Razorpay's
own dashboard/docs before the first real invoice goes out.
"""
import hmac
import hashlib
import logging
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger("pos.billing")

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


def decimal_to_paise(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def create_subscription_payment_link(invoice, owner_user=None):
    """
    Creates a Razorpay Payment Link, under Rasova's OWN account, for one
    SubscriptionInvoice. Returns (payment_link_id, payment_link_url).

    owner_user: pass the tenant's owner User if the caller already looked
    it up (e.g. generate_monthly_invoices also needs it to send the email)
    -- avoids running the same User.objects.filter(...) query twice per
    tenant. Looked up here as a fallback so this function still works
    standalone.

    Raises requests.RequestException on network/API failure -- the calling
    Celery task must catch this per-tenant so one failure doesn't block the
    rest of the monthly batch.
    """
    tenant = invoice.tenant
    # Tenant itself has no email field (checked -- only Outlet does, and this
    # is tenant-level billing, not per-outlet) -- the owner User's email is
    # the real contact address for billing correspondence.
    if owner_user is None:
        from accounts.models import User
        owner_user = User.objects.filter(tenant=tenant, role="owner").first()
    owner = owner_user.email if owner_user and owner_user.email else ""

    response = requests.post(
        f"{RAZORPAY_API_BASE}/payment_links",
        auth=(settings.RASOVA_RAZORPAY_KEY_ID, settings.RASOVA_RAZORPAY_KEY_SECRET),
        json={
            "amount": decimal_to_paise(invoice.amount),
            "currency": "INR",
            "description": (
                f"Rasova subscription — {tenant.name} "
                f"({invoice.period_start} to {invoice.period_end})"
            ),
            "customer": {"name": tenant.name, "email": owner},
            "notify": {"sms": False, "email": bool(owner)},
            "reminder_enable": True,
            "notes": {
                "invoice_id": str(invoice.id),
                "tenant_id": str(tenant.id),
                "kind": "rasova_subscription",
            },
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    return data["id"], data["short_url"]


def verify_webhook_signature(body: bytes, signature_header: str) -> bool:
    """Mirrors payments/razorpay_gateway.py's HMAC pattern exactly, checking
    against Rasova's OWN webhook secret, not any tenant's."""
    webhook_secret = settings.RASOVA_RAZORPAY_WEBHOOK_SECRET
    if not signature_header or not webhook_secret:
        return False
    expected_sig = hmac.new(
        webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_sig, signature_header)
