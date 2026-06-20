# orders/services/razorpay_gateway.py
"""
Razorpay UPI QR integration — bring-your-own-keys.

Each tenant connects their OWN Razorpay account (own Key ID + Key Secret).
Money settles directly to the restaurant's bank account; Rasova never holds
or routes funds. This keeps Rasova outside RBI's Payment Aggregator
regulatory perimeter — a marketplace/split-payment model is out of scope.

Uses `requests` directly (matches the rest of the app's convention — no SDK
anywhere). The official `razorpay` package's only real value-add, a
webhook-signature helper, is a one-line `hmac.compare_digest` already
replicated here, the same way `orders/api.py`'s aggregator webhook does it.
"""
import hmac
import hashlib
import logging
from datetime import timedelta
from decimal import Decimal

import requests
from django.utils import timezone

logger = logging.getLogger("pos.orders")

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"
QR_EXPIRY_MINUTES = 10


def paise_to_decimal(paise: int) -> Decimal:
    return Decimal(paise) / Decimal(100)


def decimal_to_paise(amount: Decimal) -> int:
    # Never round-trip through float — order.grand_total is already
    # quantized to 2 decimal places, so this is exact.
    return int((amount * 100).to_integral_value())


def create_qr_payment(order, payment_config):
    """
    Creates a single-use, fixed-amount UPI QR code for the order's current
    remaining balance. Returns the RazorpayQRCode row created to track it.

    Raises requests.RequestException on network/API failure — callers should
    catch and surface a clean error to the cashier.
    """
    from django.db.models import Sum
    from orders.models import RazorpayQRCode

    paid_total = order.payments.exclude(method="refund").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")
    remaining = order.grand_total - paid_total
    expires_at = timezone.now() + timedelta(minutes=QR_EXPIRY_MINUTES)

    response = requests.post(
        f"{RAZORPAY_API_BASE}/payments/qr_codes",
        auth=(payment_config.razorpay_key_id, payment_config.razorpay_key_secret),
        json={
            "type": "upi_qr",
            "usage": "single_use",
            "fixed_amount": True,
            "payment_amount": decimal_to_paise(remaining),
            "close_by": int(expires_at.timestamp()),
            "notes": {
                "order_id": str(order.id),
                "outlet_id": str(order.outlet_id),
            },
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    return RazorpayQRCode.objects.create(
        tenant=order.tenant,
        outlet=order.outlet,
        order=order,
        qr_code_id=data["id"],
        image_url=data["image_url"],
        quoted_amount=remaining,
        status="active",
        expires_at=expires_at,
    )


def verify_webhook_signature(body: bytes, signature_header: str, webhook_secret: str) -> bool:
    """
    Mirrors orders/api.py's HMAC verification pattern exactly.
    Razorpay sends the signature in the X-Razorpay-Signature header.
    """
    if not signature_header or not webhook_secret:
        return False
    expected_sig = hmac.new(
        webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_sig, signature_header)
