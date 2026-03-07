from django.core.management.base import BaseCommand
from tenants.models import Tenant, Outlet
from accounts.models import User
from orders.models import Table
from menu.models import MenuCategory, MenuItem
from inventory.models import InventoryItem, Recipe


class Command(BaseCommand):

    def handle(self, *args, **kwargs):

        print("Creating test tenant...")

        tenant = Tenant.objects.create(name="Test Restaurant")
        outlet = Outlet.objects.create(name="Main Outlet", tenant=tenant)

        owner = User.objects.create_user(
            username="owner",
            password="1234",
            role="owner",
            tenant=tenant,
            outlet=outlet
        )

        kitchen = User.objects.create_user(
            username="kitchen",
            password="1234",
            role="kitchen",
            tenant=tenant,
            outlet=outlet
        )

        print("Creating tables...")

        for i in range(1, 11):
            Table.objects.create(
                tenant=tenant,
                outlet=outlet,
                name=f"T{i}"
            )

        print("Creating menu...")

        cat = MenuCategory.objects.create(
            tenant=tenant,
            outlet=outlet,
            name="Main Course"
        )

        items = []

        for name, price in [
            ("Paneer Tikka", 280),
            ("Veg Fried Rice", 220),
            ("Butter Naan", 60),
            ("Dal Tadka", 180),
            ("Jeera Rice", 200)
        ]:

            item = MenuItem.objects.create(
                tenant=tenant,
                outlet=outlet,
                category=cat,
                name=name,
                price=price
            )

            items.append(item)

        print("Creating inventory...")

        rice = InventoryItem.objects.create(
            tenant=tenant,
            outlet=outlet,
            name="Rice",
            unit="g",
            stock=100000
        )

        butter = InventoryItem.objects.create(
            tenant=tenant,
            outlet=outlet,
            name="Butter",
            unit="g",
            stock=100000
        )

        print("Creating recipes...")

        for item in items:

            Recipe.objects.create(
                menu_item=item,
                inventory_item=rice,
                quantity_required=10
            )

            Recipe.objects.create(
                menu_item=item,
                inventory_item=butter,
                quantity_required=5
            )

        print("Test POS setup complete.")