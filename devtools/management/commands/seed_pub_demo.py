"""
A demo pub to record the 3-minute pub demo on (md_files/rasova_pub_selling_kit_2026-10-20.html).

Makes, or tops up, a pub tenant "Copper Tap (demo)" with everything the demo
shows: a bar station that counts drinks as made when sent, liquor in a 0%
Karnataka class, pegs with recipes in ml off 750 ml bottles, a 30 litre
lager keg, food at 5% GST, a happy hour on beer from 7 pm to 9 pm, cover
charges, tables, and staff:

    demo_pub_owner / demo_pub_manager (PIN 2580) / demo_pub_cashier / demo_pub_captain

All with the password given (--password, default "pubdemo123"). Liquor hours
are off on the demo, so it can be recorded at any time; switch them on in
Outlet Settings to show the lock.

Run locally only:  python manage.py seed_pub_demo
"""
from datetime import time
from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from inventory.models import InventoryItem, Recipe
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem, VatClass
from offers.models import Offer, OfferTarget, OfferWindow
from orders.models import Table
from orders.services.approvals import set_pin
from setup.models import KitchenStation, PaymentConfig
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant

NAME = "Copper Tap (demo)"
DRINKS = [
    # name, price, bottle, ml per drink
    ("Old Monk 30 ml", "180", "Old Monk", 30),
    ("Old Monk 60 ml", "340", "Old Monk", 60),
    ("Jameson 30 ml", "390", "Jameson", 30),
    ("Rum and Coke", "260", "Old Monk", 60),
    ("Lager pint", "320", "Lager draught", 500),
    ("Lager pitcher", "1150", "Lager draught", 1800),
]
BOTTLES = [
    # name, unit, stock, cost per unit, bottle/keg ml
    ("Old Monk", "ml", "7500", "0.85", 750),
    ("Jameson", "ml", "4500", "3.10", 750),
    ("Lager draught", "ml", "60000", "0.22", 30000),
]
FOOD = [("Chilli Chicken", "340"), ("Masala Peanuts", "160"), ("Loaded Nachos", "390")]


class Command(BaseCommand):
    help = "Make or top up the demo pub for recording the pub demo (local use)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="pubdemo123")

    @transaction.atomic
    def handle(self, *args, password, **options):
        tenant, _ = Tenant.objects.get_or_create(name=NAME, defaults={"tenant_type": "pub"})
        if tenant.tenant_type != "pub":
            tenant.tenant_type = "pub"
            tenant.save(update_fields=["tenant_type"])
        outlet, _ = Outlet.objects.get_or_create(
            tenant=tenant, name="Indiranagar",
            defaults={"gst_no": SAMPLE_GSTIN, "liquor_hours_enforced": False, "liquor_from": time(10, 0),
                      "liquor_until": time(1, 0)},
        )
        PaymentConfig.objects.get_or_create(tenant=tenant, outlet=outlet, defaults={"cash_enabled": True})

        staff = {}
        for role in ("owner", "manager", "cashier", "captain"):
            user, created = User.objects.get_or_create(
                username=f"demo_pub_{role}",
                defaults={"tenant": tenant, "outlet": outlet, "role": role, "first_name": role.title()},
            )
            if created:
                user.set_password(password)
                user.save()
            staff[role] = user
        set_pin(staff["manager"], "2580")

        KitchenStation.objects.get_or_create(tenant=tenant, outlet=outlet, name="Kitchen",
                                             defaults={"is_default": True})
        KitchenStation.objects.get_or_create(tenant=tenant, outlet=outlet, name="Bar",
                                             defaults={"made_on_send": True})
        for n in range(1, 11):
            Table.objects.get_or_create(tenant=tenant, outlet=outlet, name=f"T{n}")

        liquor = VatClass.objects.filter(outlet=outlet, name="Liquor").first() or add_liquor_class(outlet, "Liquor")
        bar, _ = MenuCategory.objects.get_or_create(tenant=tenant, outlet=outlet, name="Bar")
        kitchen, _ = MenuCategory.objects.get_or_create(tenant=tenant, outlet=outlet, name="Food")

        stock = {}
        for name, unit, qty, cost, size in BOTTLES:
            item, _ = InventoryItem.objects.get_or_create(
                tenant=tenant, outlet=outlet, name=name,
                defaults={"unit": unit, "stock": D(qty), "cost_price": D(cost), "bottle_ml": size, "category": "Bar"},
            )
            stock[name] = item
        for name, price, bottle, ml in DRINKS:
            dish, _ = MenuItem.objects.get_or_create(
                tenant=tenant, outlet=outlet, name=name,
                defaults={"category": bar, "price": D(price), "gst_percentage": D("0"), "vat_class": liquor},
            )
            Recipe.objects.get_or_create(menu_item=dish, inventory_item=stock[bottle],
                                         defaults={"quantity_required": D(ml), "unit": "ml"})
        for name, price in FOOD:
            MenuItem.objects.get_or_create(tenant=tenant, outlet=outlet, name=name,
                                           defaults={"category": kitchen, "price": D(price), "gst_percentage": D("5")})

        if not Offer.objects.filter(tenant=tenant, name="Happy hour 25% on beer").exists():
            offer = Offer.objects.create(tenant=tenant, outlet=outlet, name="Happy hour 25% on beer",
                                         kind="percent_off", percent=D("25"))
            for dish in MenuItem.objects.filter(outlet=outlet, name__startswith="Lager"):
                OfferTarget.objects.create(offer=offer, menu_item=dish)
            OfferWindow.objects.create(offer=offer, start_time=time(19, 0), end_time=time(21, 0))

        self.stdout.write(self.style.SUCCESS(
            f"{NAME} is ready. Log in as demo_pub_cashier / demo_pub_manager (PIN 2580) with the password you gave."))
