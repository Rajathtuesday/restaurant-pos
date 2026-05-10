# orders/services/order_service.py
from decimal import Decimal
from django.db import transaction, IntegrityError

from orders.models import Order, OrderItem, OrderItemModifier
from menu.models import MenuItem, Modifier

from orders.exceptions import OrderError, CartError, InventoryError, MenuItemError, ModifierError
from orders.services.event_service import log_event
from orders.services.inventory_service import check_inventory_availability


# -------------------------------------------------
# GET OR CREATE OPEN ORDER (SAFE FOR CONCURRENCY)
# -------------------------------------------------

def get_or_create_open_order(user, table, tenant=None, outlet=None):

    t = tenant or user.tenant
    o = outlet or user.outlet

    try:
        return Order.objects.get(
            tenant=t,
            outlet=o,
            table=table,
            status="open"
        )

    except Order.DoesNotExist:

        try:
            with transaction.atomic():
                order = Order.objects.create(
                    tenant=t,
                    outlet=o,
                    table=table,
                    created_by=user,
                    status="open"
                )

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
            # Another terminal created it simultaneously — fetch the winner.
            return Order.objects.get(
                tenant=t,
                outlet=o,
                table=table,
                status="open"
            )


# -------------------------------------------------
# ADD ITEMS TO ORDER
# -------------------------------------------------

@transaction.atomic
def add_items_to_order(user, order, cart_items, tenant=None, outlet=None):

    t = tenant or (user.tenant if user else order.tenant)
    o = outlet or (user.outlet if user else order.outlet)

    # Lock order row to prevent simultaneous updates
    order = (
        Order.objects
        .select_for_update()
        .get(id=order.id)
    )

    if order.status not in ["open", "billing"]:
        raise OrderError(f"Order #{order.id} is already '{order.status}' and cannot be edited.")

    if not cart_items:
        raise CartError("Cart is empty.")

    for item in cart_items:

        menu_item = MenuItem.objects.filter(
            id=item.get("id"),
            tenant=t,
            outlet=o
        ).first()

        if not menu_item:
            raise MenuItemError("Menu item not found.")

        if not menu_item.is_available:
            raise MenuItemError(f"'{menu_item.name}' is currently unavailable.")

        quantity = int(item.get("quantity", 1))

        if quantity <= 0:
            raise CartError("Quantity must be greater than zero.")

        # -------------------------------------------------
        # INVENTORY CHECK
        # -------------------------------------------------
        if not check_inventory_availability(menu_item, quantity):
            raise InventoryError(f"Insufficient inventory for '{menu_item.name}'.")

        base_price = menu_item.price * Decimal(quantity)

        order_item = OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=quantity,
            price=menu_item.price,
            item_discount_pct=Decimal(str(item.get("discount_pct", 0))),
            gst_percentage=menu_item.gst_percentage,
            total_price=base_price,
            notes=item.get("note", ""),
            is_takeaway=item.get("is_takeaway", False),
            status="review" if user is None else "pending"
        )

        # -------------------------------------------------
        # LOG EVENT
        # -------------------------------------------------

        log_event(
            order,
            "item_added",
            user,
            {
                "item": menu_item.name,
                "quantity": quantity
            }
        )

        # -------------------------------------------------
        # ADD MODIFIERS
        # -------------------------------------------------

        modifier_ids = item.get("modifiers", [])
        modifier_total = Decimal("0")

        for mod_id in modifier_ids:

            # SECURITY: filter via ModifierGroup's tenant/outlet
            modifier = Modifier.objects.filter(
                id=mod_id,
                group__tenant=t,
                group__outlet=o
            ).first()

            if not modifier:
                raise ModifierError("Modifier not found or access denied.")

            OrderItemModifier.objects.create(
                order_item=order_item,
                modifier=modifier,
                name=modifier.name,
                price=modifier.price
            )

            modifier_total += modifier.price

        # Update total price including modifiers
        if modifier_total > 0:
            total = (menu_item.price * quantity) + (modifier_total * quantity)
            order_item.total_price = total
            order_item.save(update_fields=["total_price"])

    # -------------------------------------------------
    # RECALCULATE TOTALS
    # -------------------------------------------------

    order.recalculate_totals()

    return order


# -------------------------------------------------
# UPDATE TABLE STATE
# -------------------------------------------------
def update_table_state(order):

    table = order.table

    if not table:
        return

    items = order.items.all()

    if items.filter(status__in=["pending", "sent", "preparing"]).exists():
        table.state = "preparing"

    elif items.filter(status="ready").exists():
        table.state = "ready"

    elif items.exclude(status="served").count() == 0:
        table.state = "ready"

    else:
        table.state = "ordering"

    table.save(update_fields=["state"])