# orders/services/table_transfer_service.py
from django.db import transaction
from orders.models import Order, Table


@transaction.atomic
def transfer_table(user, order_id, new_table_id):

    order = (
        Order.objects
        .select_for_update()
        .get(
            id=order_id,
            tenant=user.tenant,
            outlet=user.outlet
        )
    )

    if order.status not in ["open", "billing"]:
        raise Exception("Order cannot be transferred")

    new_table = Table.objects.get(
        id=new_table_id,
        tenant=user.tenant,
        outlet=user.outlet
    )

    old_table = order.table
    
    if old_table and old_table.id == new_table.id:
        raise Exception("Order is already on this table")

    if new_table.state not in ["free", "ordering"]:
        raise Exception("Target table is not available")

    # move order
    order.table = new_table
    order.save(update_fields=["table"])

    # free old table
    if old_table:
        old_table.state = "free"
        old_table.save(update_fields=["state"])

    # activate new table
    new_table.state = "ordering"
    new_table.save(update_fields=["state"])

    return order