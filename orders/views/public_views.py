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
from django.views.decorators.http import require_http_methods

from orders.models import Order

logger = logging.getLogger("pos.orders")

PUBLIC_BILL_SALT = "public-bill"
PUBLIC_BILL_MAX_AGE = 60 * 60 * 24 * 7  # 7 days

# order_status (menu/views/customer_views.py) used to take a raw sequential
# order_id with no login required -- anyone could enumerate it and read any
# restaurant's live order, not just their own. Same signed-token pattern as
# the bill link above, just a much shorter lifetime since this is only ever
# needed for the length of one active dine-in/QSR visit, not a receipt kept
# around for a week.
ORDER_STATUS_SALT = "order-status"
ORDER_STATUS_MAX_AGE = 60 * 60 * 6  # 6 hours


def make_public_bill_token(order_id) -> str:
    return TimestampSigner(salt=PUBLIC_BILL_SALT).sign(str(order_id))


def make_order_status_token(order_id) -> str:
    return TimestampSigner(salt=ORDER_STATUS_SALT).sign(str(order_id))


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

    from core.features import has_feature
    show_feedback_link = (
        remaining <= 0 and has_feature(order.tenant, "guest_feedback")
    )

    return render(request, "orders/public_bill.html", {
        "order": order,
        "tenant": order.tenant,
        "outlet": order.outlet,
        "remaining": remaining,
        "show_feedback_link": show_feedback_link,
        "feedback_token": signed_token if show_feedback_link else None,
    })


@require_http_methods(["GET", "POST"])
def submit_feedback(request, signed_token):
    """
    Guest-facing star rating + comment for one order, reached from a "Rate
    your experience" link on the public bill (same signed token — it's
    already order-scoped and expiry-bounded, no reason to mint a second one).
    guest_feedback is custom-only (core/features.py) and off by default, so
    an unenabled tenant gets a friendly notice, not a raw 404 -- the link
    itself may have been generated while the feature was on and shared
    before a tenant later disabled it.
    """
    from core.features import has_feature
    from crm.feedback_models import GuestFeedback

    signer = TimestampSigner(salt=PUBLIC_BILL_SALT)
    try:
        order_id = signer.unsign(signed_token, max_age=PUBLIC_BILL_MAX_AGE)
    except (SignatureExpired, BadSignature):
        return render(request, "orders/public_bill_expired.html", status=400)

    order = get_object_or_404(Order, id=order_id)

    if not has_feature(order.tenant, "guest_feedback"):
        return render(request, "orders/public_feedback.html", {
            "order": order, "tenant": order.tenant, "not_available": True,
        })

    existing = GuestFeedback.objects.filter(order=order).first()
    if existing:
        return render(request, "orders/public_feedback.html", {
            "order": order, "tenant": order.tenant, "already_submitted": True,
            "feedback": existing,
        })

    if request.method == "POST":
        try:
            rating = int(request.POST.get("rating", ""))
        except (TypeError, ValueError):
            rating = None

        if not rating or not (1 <= rating <= 5):
            return render(request, "orders/public_feedback.html", {
                "order": order, "tenant": order.tenant,
                "error": "Please pick a rating from 1 to 5 stars.",
                "guest_name": request.POST.get("guest_name", "").strip(),
                "comment": request.POST.get("comment", "").strip(),
            })

        GuestFeedback.objects.create(
            tenant=order.tenant, outlet=order.outlet, order=order,
            guest_name=request.POST.get("guest_name", "").strip(),
            rating=rating,
            comment=request.POST.get("comment", "").strip(),
        )
        return render(request, "orders/public_feedback.html", {
            "order": order, "tenant": order.tenant, "just_submitted": True,
        })

    return render(request, "orders/public_feedback.html", {
        "order": order, "tenant": order.tenant,
    })
