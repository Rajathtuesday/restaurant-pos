from django.core.exceptions import ValidationError


def validate_order_editable(order):
    """
    Prevents modification of locked orders
    """

    if order.status in ["billing", "paid", "closed"]:
        raise ValidationError("Order is locked and cannot be modified")