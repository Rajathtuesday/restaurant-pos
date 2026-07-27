# payments/refund_service.py
# Moved from orders/services/refund_service.py (Phase 6 of the orders app split).
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError

from orders.models import Payment, OrderEvent
from payments.models import Refund


@transaction.atomic
def process_refund(order, payment_id, amount, user, reason="", customer_complaint=""):
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
        customer_complaint=customer_complaint,
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
def approve_refund(refund_id, approver, tenant, outlet):
    """
    Approves a pending refund.
    - Owner only (stricter control)
    - Scoped to the approver's tenant/outlet: without this filter a refund
      could be fetched by raw id across tenants (an owner at Tenant A could
      approve Tenant B's refund and create a negative Payment against B's
      order). The tenant/outlet join is the isolation boundary.
    """
    if approver.role != "owner" and not approver.is_superuser:
        raise PermissionDenied("Only owners can approve refunds")

    refund = Refund.objects.select_for_update().get(
        id=refund_id, order__tenant=tenant, order__outlet=outlet
    )
    if refund.status != "pending":
        raise ValidationError("Refund is not in pending status")

    refund.status = "approved"
    refund.save(update_fields=["status"])

    # Create a negative Payment so revenue reports reflect net (gross - refunds).
    # daily_sales() sums Payment.amount — the negative entry cancels out the refunded amount.
    Payment.objects.create(
        order=refund.order,
        method="refund",
        amount=-refund.amount,  # negative amount
        reference=f"REFUND-{refund.id}",
        created_by=approver,
    )

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
def reject_refund(refund_id, rejecter, tenant, outlet, reason=""):
    """
    Rejects a pending refund.
    Scoped to the rejecter's tenant/outlet — same cross-tenant isolation
    boundary as approve_refund().
    """
    if rejecter.role not in ("manager", "owner") and not rejecter.is_superuser:
        raise PermissionDenied("Insufficient permissions to reject refund")

    refund = Refund.objects.select_for_update().get(
        id=refund_id, order__tenant=tenant, order__outlet=outlet
    )
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
