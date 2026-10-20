"""
The liquor stock register (inventory/liquor_register.py), 20 Oct 2026.

One day of a bar: opening stock, a KSBCL delivery, pegs sold, a broken
bottle and the night's count. The register must add up: opening + received
- sold - broken +/- count correction = closing, and the next day opens where
this one closed.

Run: python manage.py test inventory.tests.test_liquor_register
"""
import io
from datetime import timedelta
from decimal import Decimal as D

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from accounts.models import User
from core.utils import get_business_date
from inventory.bar_count import save_count
from inventory.liquor_register import register_rows
from inventory.models import InventoryItem, InventoryTransaction, Recipe
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from orders.services.inventory_service import deduct_inventory_for_items
from orders.services.order_service import add_items_to_order
from tenants.models import Outlet, Tenant, TenantFeatureOverride


class RegisterTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Register Pub", tenant_type="pub")
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no="29ABCDE1234F1Z5",
                                            liquor_hours_enforced=False)
        self.manager = User.objects.create_user(username="reg_mgr", password="pw", role="manager",
                                                tenant=self.tenant, outlet=self.outlet)
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        self.whisky = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name="Whisky 750",
                                                   unit="ml", stock=D("3000"), bottle_ml=750)
        self.peg = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=bar, name="Whisky 60 ml",
                                           price=D("400"), gst_percentage=D("0"),
                                           vat_class=add_liquor_class(self.outlet, "Spirits"))
        Recipe.objects.create(menu_item=self.peg, inventory_item=self.whisky, quantity_required=D("60"), unit="ml")
        self.today = get_business_date(timezone.now(), self.outlet)

    def test_a_day_adds_up(self):
        self.whisky.add_stock(D("1500"), reference="KSBCL INV 4471")           # 2 bottles in
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.manager,
                                     source="dine_in", status="open")
        add_items_to_order(self.manager, order, [{"id": self.peg.id, "quantity": 5}])
        deduct_inventory_for_items(list(order.items.all()))                    # 300 ml sold
        InventoryTransaction.objects.create(tenant=self.tenant, outlet=self.outlet, item=self.whisky,
                                            transaction_type="wastage", quantity=D("-750"), reference="Broken")
        InventoryItem.objects.filter(id=self.whisky.id).update(stock=D("3450"))
        self.whisky.refresh_from_db()
        save_count(self.manager, self.outlet, {self.whisky.id: ("4", "390")})  # 3,390: 60 ml short

        [row] = register_rows(self.tenant, self.outlet, self.today, self.today)
        self.assertEqual((row["opening"], row["received"], row["sold"], row["broken"], row["counted"], row["closing"]),
                         (D("3000"), D("1500"), D("300"), D("750"), D("-60"), D("3390")))
        self.assertEqual(row["opening"] + row["received"] - row["sold"] - row["broken"] + row["counted"], row["closing"])
        self.assertEqual((row["closing_bl"], row["closing_bottles"], row["closing_loose_ml"]), (D("3.390"), 4, D("390")))
        self.assertEqual(row["references"], "KSBCL INV 4471")

    def test_each_day_opens_where_the_last_closed(self):
        yesterday = self.today - timedelta(days=1)
        txn = InventoryTransaction.objects.create(tenant=self.tenant, outlet=self.outlet, item=self.whisky,
                                                  transaction_type="restock", quantity=D("750"), reference="KSBCL 1")
        InventoryItem.objects.filter(id=self.whisky.id).update(stock=D("3750"))
        InventoryTransaction.objects.filter(id=txn.id).update(created_at=timezone.now() - timedelta(days=1))
        days = register_rows(self.tenant, self.outlet, yesterday, self.today)
        self.assertEqual([(r["day"], r["opening"], r["closing"]) for r in days],
                         [(yesterday, D("3000"), D("3750")), (self.today, D("3750"), D("3750"))])

    def test_the_download_is_an_excel_register(self):
        client = Client()
        client.force_login(self.manager)
        resp = client.get(reverse("liquor_register"), {"from": self.today.isoformat(), "to": self.today.isoformat()})
        self.assertEqual(resp.status_code, 200)
        sheet = load_workbook(io.BytesIO(resp.content)).active
        self.assertEqual(sheet["B3"].value, "Brand / item")
        self.assertEqual(sheet["B4"].value, "Whisky 750")
        self.assertEqual(sheet["I4"].value, 3.0)

    def test_two_queries_however_many_bottles_and_days(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries(days):
            with CaptureQueriesContext(connection) as ctx:
                register_rows(self.tenant, self.outlet, self.today - timedelta(days=days - 1), self.today)
            return len(ctx.captured_queries)

        one = queries(1)
        for n in range(5):
            item = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"Gin {n}", unit="ml",
                                                stock=D("750"), bottle_ml=750, category="Bar")
            item.add_stock(D("750"), reference=f"KSBCL {n}")
        self.assertEqual(queries(7), one)
