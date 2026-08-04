# notifications/services/whatsapp_service.py
"""
WhatsApp receipt sending — supports two backends:

  1. Meta WhatsApp Business Cloud API (default, free tier: 1000 msg/month)
     Set in .env:  META_WHATSAPP_TOKEN, META_WHATSAPP_PHONE_ID
     Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/get-started

  2. Twilio (legacy fallback)
     Set in .env:  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM

Resolution order: Meta → Twilio → silent skip.

The service NEVER raises — if WhatsApp fails, the bill still goes through.
"""

import logging
import urllib.request
import urllib.error
import json

logger = logging.getLogger("pos.notifications")


def send_bill_receipt(order, bill_url: str = "") -> bool:
    """
    Send a WhatsApp receipt. Returns True if sent, False if skipped/failed.
    Never raises.
    """
    phone = getattr(order, "customer_phone", None)
    if not phone:
        return False

    phone = _normalize_phone(phone)
    if not phone:
        return False

    message = _build_message(order, bill_url)

    # Try Meta Cloud API first (free, official)
    if _send_meta(phone, message):
        logger.info("WhatsApp (Meta) receipt sent for order %s to %s", order.id, phone)
        return True

    # Fallback to Twilio
    if _send_twilio(phone, message, order.id):
        logger.info("WhatsApp (Twilio) receipt sent for order %s to %s", order.id, phone)
        return True

    return False


# -------------------------------------------------------
# META WHATSAPP BUSINESS CLOUD API
# -------------------------------------------------------

def _send_meta(phone: str, message: str) -> bool:
    from django.conf import settings
    token   = getattr(settings, "META_WHATSAPP_TOKEN", "")
    phone_id = getattr(settings, "META_WHATSAPP_PHONE_ID", "")
    if not (token and phone_id):
        return False

    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": message},
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning("Meta WhatsApp send failed: %s", e)
        return False


# -------------------------------------------------------
# TWILIO FALLBACK
# -------------------------------------------------------

def _send_twilio(phone: str, message: str, order_id) -> bool:
    from django.conf import settings
    sid   = getattr(settings, "TWILIO_ACCOUNT_SID", "")
    token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
    from_ = getattr(settings, "TWILIO_WHATSAPP_FROM", "")
    if not (sid and token and from_):
        return False

    try:
        from twilio.rest import Client
        client = Client(sid, token)
        client.messages.create(from_=from_, to=f"whatsapp:{phone}", body=message)
        return True
    except ImportError:
        logger.warning("Twilio not installed — pip install twilio")
        return False
    except Exception as e:
        logger.error("Twilio WhatsApp send failed for order %s: %s", order_id, e)
        return False


# -------------------------------------------------------
# RASOVA SUBSCRIPTION INVOICES (billing app)
# -------------------------------------------------------

def send_subscription_invoice(invoice, payment_link_url: str = "") -> bool:
    """
    Send a Rasova subscription invoice notice to the tenant, via WhatsApp.
    Reuses the exact same Meta/Twilio senders as customer bill receipts --
    only the message and phone lookup differ. Never raises, same as
    send_bill_receipt: a failed WhatsApp send must never block the invoice
    email or the rest of a monthly batch run.

    Known limitation, documented rather than hidden: neither Tenant nor
    User has a phone field today -- only Outlet does. This uses the
    tenant's first outlet's phone as a pragmatic stand-in. A real
    Tenant.billing_phone field would be the correct fix if this becomes
    the primary contact channel rather than a v1 stopgap.
    """
    tenant = invoice.tenant
    # order_by("id") clears Outlet's Meta.ordering (which orders by
    # tenant__name) -- left as the default, every call here would silently
    # JOIN tenants_tenant just to sort a single-row lookup, which is what
    # was quietly turning this into an N+1 across the overdue-warning loop.
    outlet = tenant.outlets.order_by("id").first()
    phone = getattr(outlet, "phone", None) if outlet else None
    if not phone:
        return False

    phone = _normalize_phone(phone)
    if not phone:
        return False

    message = _build_subscription_message(invoice, payment_link_url)

    if _send_meta(phone, message):
        logger.info("WhatsApp (Meta) subscription invoice sent for invoice %s to %s", invoice.id, phone)
        return True

    if _send_twilio(phone, message, invoice.id):
        logger.info("WhatsApp (Twilio) subscription invoice sent for invoice %s to %s", invoice.id, phone)
        return True

    return False


def _build_subscription_message(invoice, payment_link_url: str) -> str:
    lines = [
        "*Rasova Subscription Invoice*",
        "",
        f"*{invoice.tenant.name}*",
        f"Period: {invoice.period_start} to {invoice.period_end}",
        f"*Amount due: ₹{invoice.amount:.0f}*",
    ]
    if payment_link_url:
        lines += ["", f"Pay online: {payment_link_url}"]
    lines.append("\nRasova · rasova.net")
    return "\n".join(lines)


# -------------------------------------------------------
# MESSAGE BUILDER
# -------------------------------------------------------

def _build_message(order, bill_url: str) -> str:
    tenant_name = order.tenant.name if order.tenant else "Restaurant"
    lines = [
        f"*{tenant_name}*",
        f"Thank you for your visit!",
        f"",
        f"*Order #{order.order_number or order.id}*",
    ]

    try:
        for item in order.items.select_related("menu_item").all():
            lines.append(f"  {item.quantity}x {item.menu_item.name}  ₹{item.total_price:.0f}")
    except Exception as e:
        # Still send the receipt without an itemized list rather than block
        # the message entirely — but log it, so a missing item list is
        # discoverable instead of silently, permanently invisible.
        logger.warning("WhatsApp receipt: failed to build item list for order %s: %s", order.id, e)

    lines += [
        "",
        f"Subtotal: ₹{order.subtotal:.0f}" if hasattr(order, "subtotal") else "",
        f"GST:      ₹{order.tax_amount:.0f}" if hasattr(order, "tax_amount") else "",
        f"*Total:   ₹{order.grand_total:.0f}*",
    ]

    if bill_url:
        lines += ["", f"View bill: {bill_url}"]

    lines.append("\nPowered by Rasova POS")
    return "\n".join(l for l in lines if l is not None)


# -------------------------------------------------------
# PHONE NORMALIZER
# -------------------------------------------------------

def _normalize_phone(phone: str) -> str:
    """Normalize to E.164. Handles Indian 10-digit, 0-prefix, 91-prefix."""
    digits = "".join(c for c in str(phone) if c.isdigit())
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}"
    if str(phone).startswith("+") and len(digits) >= 10:
        return f"+{digits}"
    return ""
