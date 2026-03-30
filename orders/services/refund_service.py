# orders/services/refund_service.py
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal
from django.core.exceptions import ValidationError

from orders.models import Payment, OrderEvent


@transaction.atomic
def process_refund(order, payment_id, amount, user):

    payment = Payment.objects.select_for_update().get(id=payment_id, order=order)

    if payment.is_refund:
        raise ValidationError("Cannot refund a refund")

    amount = Decimal(amount)

    if amount <= 0:
        raise ValidationError("Invalid refund amount")

    # --------------------------------------------
    # 🔥 CALCULATE ALREADY REFUNDED
    # --------------------------------------------
    refunded_total = payment.refunds.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0")

    remaining = payment.amount - refunded_total

    if amount > remaining:
        raise ValidationError("Refund exceeds available amount")

    # --------------------------------------------
    # 🔥 CREATE REFUND ENTRY
    # --------------------------------------------
    refund = Payment.objects.create(
        order=order,
        method=payment.method,
        amount=amount,
        is_refund=True,
        parent_payment=payment,
        created_by=user
    )

    # --------------------------------------------
    # 🔥 AUDIT EVENT
    # --------------------------------------------
    OrderEvent.objects.create(
        tenant=order.tenant,
        outlet=order.outlet,
        order=order,
        event_type="payment_refunded",
        amount=amount,
        metadata={
            "payment_id": payment.id
        },
        created_by=user
    )

    return refund