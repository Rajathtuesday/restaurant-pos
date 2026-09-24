# orders/services/row_locks.py
from orders.models import Order, OrderItem


def lock_order_of_item(user, item_id):
    """
    Lock the order that one line belongs to, before the line itself.

    The rule for every write that touches an order and its lines: order row
    first, then line rows. Sending a ticket, taking payment and cancelling a
    whole order already work that way. Changing one line (void, reduce,
    discount, comp, kitchen status) used to lock the line first and the order
    second, so two people acting on the same order at the same moment could
    each hold what the other was waiting for. Postgres then kills one of
    them as a deadlock and that person sees a server error.

    Call this at the start of the transaction, then lock the line as usual.
    Scoped to the user's tenant and outlet; a line outside it raises
    OrderItem.DoesNotExist, same as the line query would. Returns the
    order id.
    """
    order_id = (
        OrderItem.objects
        .filter(id=item_id, order__tenant=user.tenant, order__outlet=user.outlet)
        .values_list("order_id", flat=True)
        .first()
    )
    if order_id is None:
        raise OrderItem.DoesNotExist("Order item not found.")
    Order.objects.select_for_update().filter(id=order_id).first()
    return order_id
