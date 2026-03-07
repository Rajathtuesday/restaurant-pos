from decimal import Decimal
from django.db import transaction, IntegrityError

from orders.models import Order, OrderItem, OrderItemModifier
from menu.models import MenuItem, Modifier

from orders.services.event_service import log_event


def get_or_create_open_order(user, table):

    try:

        order = Order.objects.get(
            tenant=user.tenant,
            outlet=user.outlet,
            table=table,
            status="open"
        )

        return order

    except Order.DoesNotExist:

        try:

            with transaction.atomic():

                order = Order.objects.create(
                    tenant=user.tenant,
                    outlet=user.outlet,
                    table=table,
                    created_by=user,
                    status="open"
                )

                # Log order creation
                log_event(
                    order,
                    "order_created",
                    user,
                    {
                        "table": table.name if table else "takeaway"
                    }
                )

                if table:
                    table.state = "ordering"
                    table.save(update_fields=["state"])

                return order

        except IntegrityError:

            # If another terminal created it simultaneously
            return Order.objects.get(
                tenant=user.tenant,
                outlet=user.outlet,
                table=table,
                status="open"
            )


@transaction.atomic
def add_items_to_order(user, order, cart_items):

    for item in cart_items:

        menu_item = MenuItem.objects.get(
            id=item["id"],
            tenant=user.tenant,
            outlet=user.outlet
        )

        quantity = int(item.get("quantity", 1))

        base_price = menu_item.price * quantity

        order_item = OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=quantity,
            price=menu_item.price,
            gst_percentage=menu_item.gst_percentage,
            total_price=base_price,
            status="pending"
        )

        # Log item addition
        log_event(
            order,
            "item_added",
            user,
            {
                "item": menu_item.name,
                "quantity": quantity
            }
        )

        modifier_ids = item.get("modifiers", [])

        for mod_id in modifier_ids:

            modifier = Modifier.objects.get(id=mod_id)

            OrderItemModifier.objects.create(
                order_item=order_item,
                modifier=modifier,
                name=modifier.name,
                price=modifier.price
            )

    order.recalculate_totals()

    return order


# -------------------------------------------------
# Update table state depending on order items
# -------------------------------------------------

def update_table_state(order):

    table = order.table

    if not table:
        return

    items = order.items.all()

    if not items.exists():
        table.state = "ordering"

    elif items.filter(status__in=["pending", "sent", "preparing"]).exists():
        table.state = "preparing"

    elif items.filter(status="ready").exists():
        table.state = "served"

    elif items.filter(status="served").exists():
        table.state = "served"

    table.save(update_fields=["state"])