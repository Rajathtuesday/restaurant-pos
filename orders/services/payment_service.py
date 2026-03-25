#`orders/services/payment_service.py`
from django.db import transaction
from django.db.models import Sum
from decimal import Decimal
from django.core.exceptions import ValidationError

from orders.models import Payment

from orders.utils.payment_utils import validate_order_payment


@transaction.atomic
def process_payment(order, method, amount, user=None):

    # ----------------------------
    # LOCK ORDER (critical)
    # ----------------------------
    order = type(order).objects.select_for_update().get(id=order.id)

    amount = Decimal(amount)

    if amount <= 0:
        raise ValidationError("Invalid payment amount")

    # ----------------------------
    # EXISTING PAYMENTS
    # ----------------------------
    paid_total = order.payments.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    remaining = order.grand_total - paid_total

    # ----------------------------
    # VALIDATIONS
    # ----------------------------
    if remaining <= 0:
        raise ValidationError("Order already fully paid")

    if amount > remaining:
        raise ValidationError("Payment exceeds remaining amount")

    # ----------------------------
    # CREATE PAYMENT
    # ----------------------------
    payment = Payment.objects.create(
        order=order,
        method=method,
        amount=amount,
        created_by=user
    )

    # ----------------------------
    # FINAL TOTAL CHECK
    # ----------------------------
    new_total = paid_total + amount

    if new_total >= order.grand_total:
        validate_order_payment(order) # Financial integrity check
        order.status = "paid"
        order.save(update_fields=["status"])
        print("PAID:", paid_total)
        print("NEW TOTAL:", new_total)
        print("GRAND:", order.grand_total)

    return { "payment": payment,
            "remaining": order.grand_total - new_total }