# orders/services/order_service.py
from decimal import Decimal
from django.db import transaction, IntegrityError

from orders.models import Order, OrderItem, OrderItemModifier
from menu.models import MenuItem, Modifier

from orders.exceptions import OrderError, CartError, MenuItemError, ModifierError
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

        try:
            quantity = int(item.get("quantity", 1))
        except (TypeError, ValueError):
            raise CartError("Quantity must be a valid integer.")

        if quantity <= 0:
            raise CartError("Quantity must be greater than zero.")

        # -------------------------------------------------
        # INVENTORY CHECK — warn-only by design (see check_inventory_availability's
        # docstring): it always returns True and only logs a warning, matching the
        # soft-drain-to-0 behavior at KOT time. The `raise InventoryError` this used
        # to have here could never fire and was misleading — removed rather than
        # left as dead code implying orders get blocked on low stock, which they don't.
        # -------------------------------------------------
        check_inventory_availability(menu_item, quantity)

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

    # "billing"/"cleaning" are owned by the billing & payment flows
    # (billing_core.py, payment_views.py, razorpay_views.py,
    # discount_views.py) — never overwrite them from item-status churn,
    # e.g. a manager voiding an already-served item mid-bill-review must not
    # silently bounce the table back out of "billing".
    if table.state in ("billing", "cleaning"):
        return

    # Lock the Order row so two staff cancelling different items on the same
    # table serialize correctly instead of racing on table.state.
    order = Order.objects.select_for_update().get(id=order.id)

    # Voided items are inert — they must never keep a table looking active.
    # This is the fix for the "table stuck on Ordering after everything gets
    # cancelled" bug: previously order.items.all() included voided items,
    # so a fully-voided order fell through every branch below into the
    # catch-all "ordering" instead of "free".
    items = order.items.exclude(status="voided")

    if not items.exists():
        table.state = "free"

    elif items.filter(status__in=["sent", "preparing"]).exists():
        table.state = "preparing"

    # "review" (QR guest items awaiting staff approval) gets its own branch
    # rather than folding into sent/preparing — nothing is actually cooking
    # yet, so "preparing" would be a misleading label for the kitchen.
    elif items.filter(status__in=["review", "pending"]).exists():
        table.state = "ordering"

    elif items.filter(status="ready").exists():
        table.state = "ready"

    elif items.exclude(status="served").count() == 0:
        table.state = "ready"

    else:
        table.state = "ordering"

    table.save(update_fields=["state"])