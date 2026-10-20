"""
On a box whose licence has run out (past the grace days), a NEW order
can't start. Done at the one place every order is born (Order's pre_save
for a new row), so it holds for the billing screen, tokens, QSR and any
future path alike. An order already open is never touched: it can still
get dishes, be billed and be paid.
"""
from django.db.models.signals import pre_save
from django.dispatch import receiver

from orders.exceptions import OrderError
from orders.models import Order


class LicenceEnded(OrderError):
    """The box's licence has ended; says what to do."""


@receiver(pre_save, sender=Order, dispatch_uid="box_licence_new_orders")
def refuse_new_orders_without_licence(sender, instance, **kwargs):
    from box.edition import is_box
    if not is_box() or not instance._state.adding:
        return
    from box import license
    state = license.current()
    if state.blocks_new_orders:
        raise LicenceEnded(state.message)
