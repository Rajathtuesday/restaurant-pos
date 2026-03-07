# import random
# import threading
# import time

# from django.contrib.auth import get_user_model
# from tenants.models import Tenant, Outlet
# from menu.models import MenuItem
# from orders.models import Table, Order, OrderItem

# User = get_user_model()


# def waiter_action(waiter_id):

#     tenant = Tenant.objects.first()
#     outlet = Outlet.objects.first()
#     user = User.objects.first()

#     table = Table.objects.first()

#     items = list(MenuItem.objects.all())

#     for i in range(20):

#         order = Order.objects.filter(
#             table=table,
#             status="open"
#         ).first()

#         if not order:

#             order = Order.objects.create(
#                 tenant=tenant,
#                 outlet=outlet,
#                 table=table,
#                 created_by=user,
#                 status="open"
#             )

#         item = random.choice(items)

#         OrderItem.objects.create(
#             order=order,
#             menu_item=item,
#             quantity=1,
#             price=item.price,
#             gst_percentage=item.gst_percentage,
#             total_price=item.price
#         )

#         print(f"Waiter {waiter_id} added item to Order {order.id}")

#         time.sleep(random.random())


# def run():

#     print("Starting concurrency simulation")

#     threads = []

#     for i in range(5):

#         t = threading.Thread(target=waiter_action, args=(i,))
#         threads.append(t)

#     for t in threads:
#         t.start()

#     for t in threads:
#         t.join()

#     print("Concurrency simulation finished")


import random
import threading
import time

from tenants.models import Tenant, Outlet
from accounts.models import User
from menu.models import MenuItem
from orders.models import Table

from orders.services.order_service import (
    get_or_create_open_order,
    add_items_to_order
)

from orders.services.kot_service import create_kot


THREADS = 10
ORDERS_PER_THREAD = 20


def simulate_waiter(user, tables, menu_items, waiter_id):

    print(f"Waiter {waiter_id} starting")

    for i in range(ORDERS_PER_THREAD):

        try:

            table = random.choice(tables)

            order = get_or_create_open_order(user, table)

            cart = []

            for _ in range(random.randint(1, 3)):

                item = random.choice(menu_items)

                cart.append({
                    "id": item.id,
                    "quantity": random.randint(1, 2),
                    "modifiers": []
                })

            add_items_to_order(user, order, cart)

            create_kot(user, order)

        except Exception as e:

            print(f"Waiter {waiter_id} error:", e)

        time.sleep(random.uniform(0.01, 0.05))

    print(f"Waiter {waiter_id} finished")


def run():

    print("Starting POS Concurrency Test")

    tenant = Tenant.objects.first()
    outlet = Outlet.objects.first()

    if not tenant or not outlet:
        print("No tenant/outlet found")
        return

    user = User.objects.filter(
        tenant=tenant,
        outlet=outlet
    ).first()

    if not user:
        print("No user found")
        return

    tables = list(Table.objects.filter(
        tenant=tenant,
        outlet=outlet,
        is_active=True
    ))

    if not tables:
        print("No tables found")
        return

    menu_items = list(MenuItem.objects.filter(
        tenant=tenant,
        outlet=outlet
    ))

    if not menu_items:
        print("No menu items found")
        return

    threads = []

    start_time = time.time()

    for i in range(THREADS):

        t = threading.Thread(
            target=simulate_waiter,
            args=(user, tables, menu_items, i + 1)
        )

        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    end_time = time.time()

    print("Concurrency test complete")
    print(f"Threads: {THREADS}")
    print(f"Orders per thread: {ORDERS_PER_THREAD}")
    print(f"Total orders attempted: {THREADS * ORDERS_PER_THREAD}")
    print(f"Time taken: {round(end_time - start_time, 2)} seconds")