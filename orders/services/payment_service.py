from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from orders.models import Payment


@transaction.atomic
def process_payment(order, method, amount=None):

    Payment.objects.create(
        order=order,
        method=method,
        amount=order.grand_total if amount is None else 0  # Treat null as full payment for time being 
    )

    total_paid = sum(p.amount for p in order.payments.all())

    if total_paid >= order.grand_total:

        order.status = "closed"
        order.closed_at = timezone.now()
        order.save(update_fields=["status", "closed_at"])

        if order.table:
            order.table.state = "cleaning"
            order.table.save(update_fields=["state"])