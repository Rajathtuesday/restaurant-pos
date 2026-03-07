import random
from decimal import Decimal

from tenants.models import Tenant, Outlet
from accounts.models import User
from orders.models import Table
from menu.models import MenuCategory, MenuItem
from inventory.models import InventoryItem, Recipe


def run():

    tenant = Tenant.objects.first()
    outlet = Outlet.objects.first()

    print("Creating tables...")

    for i in range(1, 16):
        Table.objects.get_or_create(
            tenant=tenant,
            outlet=outlet,
            name=f"T{i}"
        )

    print("Tables created")


    print("Creating menu categories...")

    categories = [
        "Starters",
        "Main Course",
        "Breads",
        "Rice",
        "Desserts",
        "Beverages"
    ]

    category_objs = []

    for name in categories:
        c, _ = MenuCategory.objects.get_or_create(
            tenant=tenant,
            outlet=outlet,
            name=name
        )
        category_objs.append(c)

    print("Categories created")


    print("Creating menu items...")

    items = [
        ("Paneer Tikka", 280),
        ("Gobi Manchurian", 220),
        ("Veg Spring Roll", 180),

        ("Paneer Butter Masala", 320),
        ("Veg Kadai", 300),
        ("Dal Tadka", 240),

        ("Butter Naan", 60),
        ("Garlic Naan", 70),
        ("Tandoori Roti", 40),

        ("Veg Fried Rice", 220),
        ("Jeera Rice", 180),
        ("Paneer Biryani", 300),

        ("Gulab Jamun", 120),
        ("Ice Cream", 100),

        ("Masala Coke", 80),
        ("Sweet Lime Soda", 90),
        ("Mineral Water", 40)
    ]

    menu_items = []

    for name, price in items:

        category = random.choice(category_objs)

        item, _ = MenuItem.objects.get_or_create(
            tenant=tenant,
            outlet=outlet,
            category=category,
            name=name,
            price=Decimal(price),
            gst_percentage=Decimal("5")
        )

        menu_items.append(item)

    print("Menu items created")


    print("Creating inventory items...")

    inventory_items = [
        "Paneer",
        "Onion",
        "Tomato",
        "Capsicum",
        "Garlic",
        "Ginger",
        "Rice",
        "Flour",
        "Oil",
        "Butter",
        "Milk",
        "Sugar",
        "Spices"
    ]

    inventory_objs = []

    for name in inventory_items:

        item, _ = InventoryItem.objects.get_or_create(
            tenant=tenant,
            outlet=outlet,
            name=name,
            unit="g",
            stock=Decimal("10000"),
            low_stock_threshold=Decimal("500")
        )

        inventory_objs.append(item)

    print("Inventory created")


    print("Creating recipes...")

    for menu_item in menu_items:

        ingredients = random.sample(inventory_objs, 3)

        for ing in ingredients:

            Recipe.objects.get_or_create(
                menu_item=menu_item,
                inventory_item=ing,
                quantity_required=Decimal(random.randint(50,150))
            )

    print("Recipes created")

    print("Seed data ready!")