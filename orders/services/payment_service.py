#`orders/services/payment_service.py`
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from orders.models import Payment



@transaction.atomic
def process_payment(order, method, amount):

    amount = Decimal(amount)

    Payment.objects.create(
        order=order,
        method=method,
        amount=amount
    )

    total_paid = sum(p.amount for p in order.payments.all())

    if total_paid >= order.grand_total:

        order.status = "paid"
        order.save(update_fields=["status"])

        if order.table:
            order.table.state = "cleaning"
            order.table.save(update_fields=["state"])