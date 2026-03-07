# orders/management/commands/test_pos_flow.py
from django.core.management.base import BaseCommand
from django.utils import timezone

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
from orders.services.payment_service import process_payment


class Command(BaseCommand):

    help = "Simulate a full POS lifecycle flow"

    def handle(self, *args, **kwargs):

        self.stdout.write(self.style.WARNING("Starting POS simulation..."))

        # -------------------------------------------------
        # Find tenant + outlet
        # -------------------------------------------------

        tenant = Tenant.objects.first()
        outlet = Outlet.objects.first()

        if not tenant or not outlet:
            self.stdout.write(self.style.ERROR("No tenant or outlet found"))
            return

        # -------------------------------------------------
        # Find or create test user
        # -------------------------------------------------

        user = User.objects.filter(
            tenant=tenant,
            outlet=outlet
        ).first()

        if not user:
            user = User.objects.create(
                username="pos_tester",
                tenant=tenant,
                outlet=outlet
            )

        # -------------------------------------------------
        # Find table
        # -------------------------------------------------

        table = Table.objects.filter(
            tenant=tenant,
            outlet=outlet,
            is_active=True
        ).first()

        if not table:
            self.stdout.write(self.style.ERROR("No table found"))
            return

        # -------------------------------------------------
        # Find menu items
        # -------------------------------------------------

        menu_items = MenuItem.objects.filter(
            tenant=tenant,
            outlet=outlet
        )[:3]

        if not menu_items.exists():
            self.stdout.write(self.style.ERROR("No menu items found"))
            return

        # -------------------------------------------------
        # Create cart
        # -------------------------------------------------

        cart = []

        for item in menu_items:
            cart.append({
                "id": item.id,
                "quantity": 1,
                "modifiers": []
            })

        # -------------------------------------------------
        # Create order
        # -------------------------------------------------

        self.stdout.write("Creating order...")

        order = get_or_create_open_order(user, table)

        # -------------------------------------------------
        # Add items
        # -------------------------------------------------

        self.stdout.write("Adding items to order...")

        add_items_to_order(user, order, cart)

        # -------------------------------------------------
        # Send to kitchen
        # -------------------------------------------------

        self.stdout.write("Sending to kitchen...")

        kot = create_kot(user, order)

        # -------------------------------------------------
        # Kitchen preparing
        # -------------------------------------------------

        self.stdout.write("Kitchen preparing items...")

        items = OrderItem.objects.filter(order=order)

        for item in items:
            item.status = "preparing"
            item.save(update_fields=["status"])

        update_table_state(order)

        # -------------------------------------------------
        # Kitchen ready
        # -------------------------------------------------

        self.stdout.write("Kitchen marking items ready...")

        for item in items:
            item.status = "ready"
            item.save(update_fields=["status"])

        update_table_state(order)

        # -------------------------------------------------
        # Waiter serves
        # -------------------------------------------------

        self.stdout.write("Serving items...")

        for item in items:
            item.status = "served"
            item.save(update_fields=["status"])

        update_table_state(order)

        # -------------------------------------------------
        # Generate bill
        # -------------------------------------------------

        self.stdout.write("Generating bill...")

        order.recalculate_totals()

        # -------------------------------------------------
        # Payment
        # -------------------------------------------------

        self.stdout.write("Processing payment...")

        process_payment(order, "cash", order.grand_total)

        # -------------------------------------------------
        # Clean table
        # -------------------------------------------------

        table.state = "cleaning"
        table.save(update_fields=["state"])

        table.state = "free"
        table.save(update_fields=["state"])

        self.stdout.write(self.style.SUCCESS("POS simulation complete!"))