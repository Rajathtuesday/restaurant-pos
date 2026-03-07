import random
import time

from django.contrib.auth import get_user_model
from tenants.models import Tenant, Outlet
from menu.models import MenuCategory, MenuItem
from orders.models import Table, Order, OrderItem, WaiterCall, KOTBatch

User = get_user_model()


def run():

    print("Starting Restaurant Rush Simulation")

    tenant = Tenant.objects.first()
    outlet = Outlet.objects.first()
    user = User.objects.first()

    tables = list(Table.objects.all())
    menu_items = list(MenuItem.objects.all())

    if not tables or not menu_items:
        print("No tables or menu items found")
        return

    orders_created = 0
    items_created = 0
    waiter_calls = 0

    start_time = time.time()

    for i in range(150):

        table = random.choice(tables)

        order = Order.objects.filter(
            table=table,
            status="open"
        ).first()

        if not order:

            order = Order.objects.create(
                tenant=tenant,
                outlet=outlet,
                table=table,
                created_by=user,
                status="open"
            )

        cart_size = random.randint(1, 5)

        for _ in range(cart_size):

            item = random.choice(menu_items)

            quantity = random.randint(1, 3)

            total = item.price * quantity

            OrderItem.objects.create(
                order=order,
                menu_item=item,
                quantity=quantity,
                price=item.price,
                gst_percentage=item.gst_percentage,
                total_price=total
            )

            items_created += 1

        if random.random() < 0.5:

            kot = KOTBatch.objects.create(
                order=order,
                status="confirmed"
            )

            order_items = OrderItem.objects.filter(order=order, kot__isnull=True)

            for item in order_items:
                item.kot = kot
                item.save()

            order.status = "confirmed"
            order.save()

        if random.random() < 0.2:

            WaiterCall.objects.create(
                tenant=tenant,
                outlet=outlet,
                table=table
            )

            waiter_calls += 1

        orders_created += 1

    end_time = time.time()

    print("Simulation Complete")
    print("----------------------")
    print("Orders Created:", orders_created)
    print("Items Created:", items_created)
    print("Waiter Calls:", waiter_calls)
    print("Time Taken:", round(end_time - start_time, 2), "seconds")