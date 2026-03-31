# orders/services/refund_service.py
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError

from orders.models import Payment, Refund, OrderEvent


@transaction.atomic
def process_refund(order, payment_id, amount, user):
    """
    Issues a refund against a specific payment.
    - Manager/Owner only
    - Amount cannot exceed remaining refundable amount on the payment
    """
    if user.role not in ("manager", "owner") and not user.is_superuser:
        raise PermissionDenied("Only managers or owners can issue refunds")

    payment = Payment.objects.select_for_update().get(id=payment_id, order=order)

    amount = Decimal(str(amount))

    if amount <= 0:
        raise ValidationError("Invalid refund amount")

    # How much has already been refunded against this payment?
    refunded_total = payment.refunds.filter(status="approved").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")

    remaining = payment.amount - refunded_total

    if amount > remaining:
        raise ValidationError(f"Refund exceeds available amount. Max: ₹{remaining:.2f}")

    # Create Refund record
    refund = Refund.objects.create(
        payment=payment,
        order=order,
        amount=amount,
        reason="Manager refund",
        status="approved",
        refunded_by=user,
    )

    # Audit event
    OrderEvent.objects.create(
        tenant=order.tenant,
        outlet=order.outlet,
        order=order,
        event_type="payment_refunded",
        amount=amount,
        metadata={"payment_id": payment.id, "refund_id": refund.id, "refunded_by": user.username},
        created_by=user,
    )

    return refund