"""
The aggregator webhook's signature (orders/api.py::api_ingest_order).

A caller sends two headers:

    X-Timestamp: the Unix time it sent the request, in whole seconds
    X-Signature: hex HMAC-SHA256, keyed with the outlet's webhook secret,
                 of "<X-Timestamp>.<raw request body>"

The timestamp is signed with the body, so a captured request can't be
replayed later with a fresh timestamp; a request older or newer than
settings.AGGREGATOR_WEBHOOK_MAX_AGE_SECONDS is refused. A replay inside
that window is caught by the order ID, which every webhook order must
carry and which is unique per outlet.

The view, scripts/simulate_aggregator_order.py and the tests all sign
through this module, so they can't disagree about the format.
"""
import hashlib
import hmac
import time

from django.conf import settings

TIMESTAMP_HEADER = "X-Timestamp"
SIGNATURE_HEADER = "X-Signature"


def sign(secret, timestamp, body):
    """Hex HMAC-SHA256 of "<timestamp>.<body>" (body as bytes)."""
    message = str(int(timestamp)).encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def signed_headers(secret, body, now=None):
    """The two headers a caller sends with `body`."""
    timestamp = int(time.time() if now is None else now)
    return {TIMESTAMP_HEADER: str(timestamp), SIGNATURE_HEADER: sign(secret, timestamp, body)}


def verify(secret, timestamp, signature, body, now=None):
    """True only for a fresh timestamp and a matching signature."""
    if not secret or not timestamp or not signature:
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = time.time() if now is None else now
    if abs(now - sent_at) > settings.AGGREGATOR_WEBHOOK_MAX_AGE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, sent_at, body), str(signature))
