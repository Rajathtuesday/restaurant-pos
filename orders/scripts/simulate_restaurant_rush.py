import random
import time
from decimal import Decimal

from tenants.models import Tenant, Outlet
from accounts.models import User
from orders.models import Order, OrderItem, Table, KOTBatch
from menu.models import MenuItem


def run():

    tenant = Tenant.objects.first()
    outlet = Outlet.objects.first()
    waiter = User.objects.filter(role="waiter").first()

    tables = list(Table.objects.filter(outlet=outlet))
    menu_items = list(MenuItem.objects.filter(outlet=outlet))

    orders_created = 0
    items_created = 0

    start = time.time()

    print("Starting Restaurant Rush Simulation")

    for i in range(1000):

        table = random.choice(tables)

        order = Order.objects.create(
            tenant=tenant,
            outlet=outlet,
            table=table,
            created_by=waiter,
            status="open"
        )

        item_count = random.randint(1,5)

        for _ in range(item_count):

            menu_item = random.choice(menu_items)
            qty = random.randint(1,3)

            subtotal = menu_item.price * qty
            gst = subtotal * (menu_item.gst_percentage / Decimal("100"))

            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=qty,
                price=menu_item.price,
                gst_percentage=menu_item.gst_percentage,
                total_price=subtotal + gst
            )

            items_created += 1

        kot = KOTBatch.objects.create(
            order=order,
            status="confirmed"
        )

        OrderItem.objects.filter(order=order).update(kot=kot)

        order.status = "confirmed"
        order.save()

        orders_created += 1

    end = time.time()

    print("Simulation Complete")
    print("----------------------")
    print("Orders Created:", orders_created)
    print("Items Created:", items_created)
    print("Time Taken:", round(end-start,2), "seconds")
    