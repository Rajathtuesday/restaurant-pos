# orders/services/payment_service.py
# ============================================================
# Changes vs original:
#
# 1. Overpayment allowed (QSR cash reality):
#    BEFORE: raises ValidationError if amount > remaining
#    AFTER:  records only `remaining` as amount, returns change_due
#    WHY recording `remaining` not the tendered amount:
#      Recording ₹500 for a ₹480 bill makes the Z-report show ₹500
#      cash collected — the restaurant never kept that ₹20.
#
# 2. paid → closed in one place:
#    BEFORE: service set "paid", view set "closed" — callers that
#            skipped the view left orders stuck at "paid".
#    AFTER:  service transitions all the way to "closed" and sets
#            closed_at. Returns order_closed=True for callers.
#
# 3. Row-locked order (already existed, kept as-is).
# ============================================================

import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from orders.models import Payment

logger = logging.getLogger("pos.orders")


@transaction.atomic
def process_payment(order, method, amount, user=None):
    """
    Records a payment against an order.

    Parameters
    ----------
    order  : Order instance (will be re-fetched under lock)
    method : "cash" | "upi" | "card"
    amount : Decimal — the amount TENDERED (may exceed remaining for cash)
    user   : User instance or None

    Returns
    -------
    {
        "payment":      Payment instance,
        "remaining":    Decimal — balance still due after this payment (≥0),
        "change_due":   Decimal — change to return to the customer (≥0),
        "order_closed": bool   — True if this payment fully settled the order,
    }

    Raises
    ------
    ValidationError  — zero/negative amount, or order already fully paid.
    """
    # Lock the order row to prevent concurrent over-payment.
    order = type(order).objects.select_for_update().get(id=order.id)

    amount = Decimal(str(amount))

    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")

    paid_total = (
        order.payments
        .exclude(method="refund")
        .aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )
    remaining = order.grand_total - paid_total

    if remaining <= 0:
        raise ValidationError("This order is already fully paid.")

    # ---------------------------------------------------------------
    # Overpayment: common when a customer hands over a ₹500 note.
    # We record only what was kept; change_due tells the cashier
    # how much to give back.
    # ---------------------------------------------------------------
    if amount > remaining:
        change_due       = amount - remaining
        amount_to_record = remaining
    else:
        change_due       = Decimal("0.00")
        amount_to_record = amount

    payment = Payment.objects.create(
        order=order,
        method=method,
        amount=amount_to_record,
        created_by=user,
    )

    logger.info(
        "Payment | order=%s | method=%s | tendered=%.2f | "
        "recorded=%.2f | change=%.2f | user=%s",
        order.id, method, float(amount), float(amount_to_record),
        float(change_due), getattr(user, "username", "system"),
    )

    # ---------------------------------------------------------------
    # Close the order if it is now fully settled.
    # This single location ensures split_pay, token billing, and any
    # future callers all produce a consistent "closed" state.
    # ---------------------------------------------------------------
    new_paid_total = paid_total + amount_to_record
    order_closed   = False

    if new_paid_total >= order.grand_total:
        order.status    = "closed"
        order.closed_at = timezone.now()
        order.save(update_fields=["status", "closed_at"])
        order_closed = True
        logger.info("Order #%s fully paid and closed.", order.id)

    return {
        "payment":      payment,
        "remaining":    max(Decimal("0.00"), order.grand_total - new_paid_total),
        "change_due":   change_due,
        "order_closed": order_closed,
    }