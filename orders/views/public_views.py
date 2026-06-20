# orders/views/public_views.py
"""
Public, login-free views — reachable by customers via a signed link (e.g. the
WhatsApp bill receipt). Never key anything in here off request.user.
"""
import logging
from decimal import Decimal

from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.db.models import Sum
from django.shortcuts import render, get_object_or_404

from orders.models import Order

logger = logging.getLogger("pos.orders")

PUBLIC_BILL_SALT = "public-bill"
PUBLIC_BILL_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def make_public_bill_token(order_id) -> str:
    return TimestampSigner(salt=PUBLIC_BILL_SALT).sign(str(order_id))


def public_bill(request, signed_token):
    signer = TimestampSigner(salt=PUBLIC_BILL_SALT)
    try:
        order_id = signer.unsign(signed_token, max_age=PUBLIC_BILL_MAX_AGE)
    except SignatureExpired:
        return render(request, "orders/public_bill_expired.html", status=410)
    except BadSignature:
        return render(request, "orders/public_bill_expired.html", status=400)

    order = get_object_or_404(Order, id=order_id)
    paid_total = order.payments.exclude(method="refund").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")
    remaining = order.grand_total - paid_total

    return render(request, "orders/public_bill.html", {
        "order": order,
        "tenant": order.tenant,
        "outlet": order.outlet,
        "remaining": remaining,
    })
