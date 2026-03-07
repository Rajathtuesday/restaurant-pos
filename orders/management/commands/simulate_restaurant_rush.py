import random
import time

from django.core.management.base import BaseCommand

from tenants.models import Tenant, Outlet
from accounts.models import User
from menu.models import MenuItem
from orders.models import Table, OrderItem

from orders.services.order_service import (
    get_or_create_open_order,
    add_items_to_order,
    update_table_state
)

from orders.services.kot_service import create_kot


class Command(BaseCommand):

    help = "Simulate restaurant rush"

    def handle(self, *args, **kwargs):

        print("Starting Restaurant Rush Simulation")

        tenant = Tenant.objects.first()
        outlet = Outlet.objects.first()

        if not tenant or not outlet:
            print("No tenant or outlet found")
            return

        user = User.objects.filter(
            tenant=tenant,
            outlet=outlet
        ).first()

        if not user:
            print("No user found")
            return

        tables = Table.objects.filter(
            tenant=tenant,
            outlet=outlet,
            is_active=True
        )

        if not tables.exists():
            print("No tables found")
            return

        menu_items = MenuItem.objects.filter(
            tenant=tenant,
            outlet=outlet
        )

        if not menu_items.exists():
            print("No menu items found")
            return

        tables = list(tables)
        menu_items = list(menu_items)

        orders_created = 0

        for i in range(30):

            table = random.choice(tables)

            # create order
            order = get_or_create_open_order(user, table)

            # build random cart
            cart = []

            for _ in range(random.randint(1, 4)):

                item = random.choice(menu_items)

                cart.append({
                    "id": item.id,
                    "quantity": random.randint(1, 3),
                    "modifiers": []
                })

            # add items
            add_items_to_order(user, order, cart)

            # send to kitchen
            try:
                create_kot(user, order)
            except Exception:
                continue

            # simulate kitchen preparing
            items = OrderItem.objects.filter(order=order)

            for item in items:
                item.status = "preparing"
                item.save(update_fields=["status"])

            update_table_state(order)

            # simulate ready
            for item in items:
                item.status = "ready"
                item.save(update_fields=["status"])

            update_table_state(order)

            # simulate serving
            for item in items:
                item.status = "served"
                item.save(update_fields=["status"])

            update_table_state(order)

            orders_created += 1

            time.sleep(random.uniform(0.05, 0.2))

        print(f"Rush simulation finished ({orders_created} orders processed)")