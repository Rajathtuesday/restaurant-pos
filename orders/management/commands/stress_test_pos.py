# import random
# import threading

# from django.core.management.base import BaseCommand
# from django.contrib.auth import get_user_model
# from django.db import transaction

# from tenants.models import Tenant, Outlet
# from orders.models import Table, Order
# from menu.models import MenuCategory, MenuItem

# from orders.services.order_service import get_or_create_open_order, add_items_to_order
# from orders.services.kot_service import create_kot
# from orders.services.payment_service import process_payment


# User = get_user_model()


# class Command(BaseCommand):

#     help = "Full POS stress test with automatic setup and cleanup"

#     def handle(self, *args, **kwargs):

#         self.stdout.write("Starting POS stress test")

#         # --------------------------------------------------
#         # Ensure tenant + outlet exist
#         # --------------------------------------------------

#         tenant = Tenant.objects.first()
#         if not tenant:
#             tenant = Tenant.objects.create(name="StressTenant")
#             self.stdout.write("Created test tenant")

#         outlet = Outlet.objects.filter(tenant=tenant).first()
#         if not outlet:
#             outlet = Outlet.objects.create(tenant=tenant, name="Main Outlet")
#             self.stdout.write("Created test outlet")

#         # --------------------------------------------------
#         # Ensure user exists
#         # --------------------------------------------------

#         user = User.objects.filter(tenant=tenant).first()

#         if not user:
#             user = User.objects.create(
#                 username="stress_user",
#                 tenant=tenant,
#                 outlet=outlet
#             )
#             user.set_password("test")
#             user.save()

#             self.stdout.write("Created test user")

#         # --------------------------------------------------
#         # Ensure tables exist
#         # --------------------------------------------------

#         tables = Table.objects.filter(tenant=tenant, outlet=outlet)

#         if not tables.exists():

#             for i in range(10):

#                 Table.objects.create(
#                     tenant=tenant,
#                     outlet=outlet,
#                     name=f"StressTable{i+1}"
#                 )

#             tables = Table.objects.filter(tenant=tenant, outlet=outlet)

#             self.stdout.write("Created stress test tables")

#         # --------------------------------------------------
#         # Ensure menu items exist
#         # --------------------------------------------------

#         menu_items = MenuItem.objects.filter(tenant=tenant, outlet=outlet)

#         if not menu_items.exists():

#             category = MenuCategory.objects.create(
#                 tenant=tenant,
#                 outlet=outlet,
#                 name="Stress Category"
#             )

#             for name, price in [
#                 ("Burger", 200),
#                 ("Fries", 120),
#                 ("Coke", 80),
#                 ("Pizza", 300),
#                 ("Beer", 250)
#             ]:

#                 MenuItem.objects.create(
#                     tenant=tenant,
#                     outlet=outlet,
#                     category=category,
#                     name=name,
#                     price=price
#                 )

#             menu_items = MenuItem.objects.filter(tenant=tenant, outlet=outlet)

#             self.stdout.write("Created stress menu items")

#         # --------------------------------------------------
#         # Stress Simulation
#         # --------------------------------------------------

#         created_orders = []

#         def simulate_table(table):

#             try:

#                 order = get_or_create_open_order(user, table)

#                 created_orders.append(order.id)

#                 # First round items
#                 cart = []

#                 for _ in range(random.randint(1, 3)):

#                     item = random.choice(list(menu_items))

#                     cart.append({
#                         "id": item.id,
#                         "quantity": random.randint(1, 2),
#                         "modifiers": []
#                     })

#                 add_items_to_order(user, order, cart)

#                 create_kot(user, order)

#                 # Second round
#                 cart2 = []

#                 for _ in range(random.randint(1, 2)):

#                     item = random.choice(list(menu_items))

#                     cart2.append({
#                         "id": item.id,
#                         "quantity": 1,
#                         "modifiers": []
#                     })

#                 add_items_to_order(user, order, cart2)

#                 create_kot(user, order)

#                 process_payment(order, "cash", order.grand_total)

#             except Exception as e:

#                 self.stdout.write(self.style.ERROR(str(e)))

#         # --------------------------------------------------
#         # Launch threads (simulate waiters)
#         # --------------------------------------------------

#         threads = []

#         for table in tables[:10]:

#             t = threading.Thread(target=simulate_table, args=(table,))
#             threads.append(t)
#             t.start()

#         for t in threads:
#             t.join()

#         self.stdout.write(self.style.SUCCESS("Simulation finished"))

#         # --------------------------------------------------
#         # Cleanup
#         # --------------------------------------------------

#         self.stdout.write("Cleaning up test orders")

#         with transaction.atomic():

#             Order.objects.filter(id__in=created_orders).delete()

#         self.stdout.write(self.style.SUCCESS("Cleanup complete"))
#         self.stdout.write(self.style.SUCCESS("POS stress test finished"))




# =========================v2========================
import random

from django.core.management.base import BaseCommand
from accounts.models import User
from orders.models import Table, Order
from menu.models import MenuItem

from orders.services.order_service import (
    get_or_create_open_order,
    add_items_to_order
)

from orders.services.kot_service import create_kot
from orders.services.payment_service import process_payment


class Command(BaseCommand):

    def handle(self, *args, **kwargs):

        print("Starting POS stress test")

        user = User.objects.filter(role="owner").first()

        tables = list(Table.objects.all())
        items = list(MenuItem.objects.all())

        for cycle in range(50):

            table = random.choice(tables)

            order = get_or_create_open_order(user, table)

            cart = []

            for _ in range(random.randint(1,4)):

                item = random.choice(items)

                cart.append({
                    "id": item.id,
                    "quantity": random.randint(1,3),
                    "modifiers":[]
                })

            add_items_to_order(user, order, cart)

            kot = create_kot(user, order)

            kot.items.update(status="preparing")

            kot.items.update(status="ready")

            kot.items.update(status="served")

            order.recalculate_totals()

            if random.random() < 0.3:
                # customer orders more
                add_items_to_order(user, order, cart)
                create_kot(user, order)

            if random.random() < 0.5:
                process_payment(order, "cash")

            print(f"Cycle {cycle} completed")

        print("Stress test finished")