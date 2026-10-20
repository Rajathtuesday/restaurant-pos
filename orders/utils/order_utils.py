from django.core.exceptions import ValidationError


def validate_order_editable(order):
    """
    Refuses changes to a bill that is paid, closed or cancelled.

    A printed bill (status "billing") may still change; lowering it needs a
    manager's approval, which the caller checks (orders/services/approvals.py).
    Until 20 Oct 2026 this refused "billing" too, for everyone, while a bill
    discount on a printed bill was allowed for anyone.
    """

    if order.status in ["paid", "closed", "cancelled"]:
        raise ValidationError("Order is locked and cannot be modified")
