# orders/services/refund_service.py
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError

from orders.models import Payment, Refund, OrderEvent


@transaction.atomic
def process_refund(order, payment_id, amount, user, reason="Manager refund"):
    """
    Requests a refund. Defaults to 'pending'.
    - Manager/Owner only
    - Amount cannot exceed remaining refundable amount on the payment
    """
    if user.role not in ("manager", "owner") and not user.is_superuser:
        raise PermissionDenied("Only managers or owners can initiate refunds")

    payment = Payment.objects.select_for_update().get(id=payment_id, order=order)
    amount = Decimal(str(amount))

    if amount <= 0:
        raise ValidationError("Invalid refund amount")

    # How much has already been refunded (approved or pending) against this payment?
    # We include pending to prevent double-refund requests
    refunded_total = payment.refunds.exclude(status="rejected").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")

    remaining = payment.amount - refunded_total

    if amount > remaining:
        raise ValidationError(f"Refund request exceeds available amount. Max: ₹{remaining:.2f}")

    # Create Refund record
    refund = Refund.objects.create(
        payment=payment,
        order=order,
        amount=amount,
        reason=reason,
        status="pending",
        refunded_by=user,
    )

    # Audit event
    OrderEvent.objects.create(
        tenant=order.tenant,
        outlet=order.outlet,
        order=order,
        event_type="payment_refund_requested",
        amount=amount,
        metadata={"payment_id": payment.id, "refund_id": refund.id, "requester": user.username},
        created_by=user,
    )

    return refund

@transaction.atomic
def approve_refund(refund_id, approver):
    """
    Approves a pending refund.
    - Owner only (stricter control)
    """
    if approver.role != "owner" and not approver.is_superuser:
        raise PermissionDenied("Only owners can approve refunds")

    refund = Refund.objects.select_for_update().get(id=refund_id)
    if refund.status != "pending":
        raise ValidationError("Refund is not in pending status")

    refund.status = "approved"
    refund.save(update_fields=["status"])

    # Audit event
    OrderEvent.objects.create(
        tenant=refund.order.tenant,
        outlet=refund.order.outlet,
        order=refund.order,
        event_type="payment_refunded",
        amount=refund.amount,
        metadata={"payment_id": refund.payment_id, "refund_id": refund.id, "approved_by": approver.username},
        created_by=approver,
    )
    return refund

@transaction.atomic
def reject_refund(refund_id, rejecter, reason=""):
    """
    Rejects a pending refund.
    """
    if rejecter.role not in ("manager", "owner") and not rejecter.is_superuser:
        raise PermissionDenied("Insufficient permissions to reject refund")

    refund = Refund.objects.select_for_update().get(id=refund_id)
    if refund.status != "pending":
        raise ValidationError("Refund is not in pending status")

    refund.status = "rejected"
    if reason:
        refund.reason = f"{refund.reason} (Rejected: {reason})"
    refund.save(update_fields=["status", "reason"])

    # Audit event
    OrderEvent.objects.create(
        tenant=refund.order.tenant,
        outlet=refund.order.outlet,
        order=refund.order,
        event_type="payment_refund_rejected",
        amount=refund.amount,
        metadata={"refund_id": refund.id, "rejected_by": rejecter.username, "reason": reason},
        created_by=rejecter,
    )
    return refund