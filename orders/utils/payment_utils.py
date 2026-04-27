from django.db.models import Sum
from decimal import Decimal
from django.core.exceptions import ValidationError


def validate_order_payment(order):
    """
    Ensures total payments == order grand total
    This is a financial integrity check.
    """

    paid = order.payments.exclude(method="refund").aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    TOLERANCE = Decimal("0.00")

    if abs(paid - order.grand_total) > TOLERANCE:
        raise ValidationError(
            f"Financial mismatch: Paid={paid}, Expected={order.grand_total}"
        )

    return True