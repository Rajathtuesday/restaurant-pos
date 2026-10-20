"""
The nightly bar count (inventory/bar_count.py), 20 Oct 2026.

A free pour or a drink sold for cash and never rung up takes ml out of the
bottle but not out of Rasova's stock. The variance report compares the
till's deductions with the recipes, so it shows nothing; only counting the
bottles does. These tests pin that down, then the count itself: blind,
sealed bottles plus the open one, the gap in ml, pegs and rupees, and the
stock set to what was counted.

Run: python manage.py test inventory.tests.test_bar_count
"""
from decimal import Decimal as D

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from inventory.bar_count import CountError, bar_items, count_report, save_count, sell_prices
from inventory.models import BarCount, InventoryItem, InventoryTransaction, Recipe
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from orders.services.inventory_service import deduct_inventory_for_items
from orders.services.order_service import add_items_to_order
from tenants.models import Outlet, Tenant, TenantFeatureOverride


class Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Count Pub", tenant_type="pub")
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no="29ABCDE1234F1Z5",
                                            liquor_hours_enforced=False)
        self.manager = User.objects.create_user(username="count_mgr", password="pw", role="manager",
                                                tenant=self.tenant, outlet=self.outlet)
        self.cashier = User.objects.create_user(username="count_cashier", password="pw", role="cashier",
                                                tenant=self.tenant, outlet=self.outlet)
        spirits = add_liquor_class(self.outlet, "Spirits")
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.rum = self.stock("Old Monk", "ml", "7500", cost="1.20", bottle=750)
        self.peg = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=bar, name="Old Monk 30 ml",
                                           price=D("300"), gst_percentage=D("0"), vat_class=spirits)
        Recipe.objects.create(menu_item=self.peg, inventory_item=self.rum, quantity_required=D("30"), unit="ml")
        self.cocktail = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=bar,
                                                name="Rum Punch", price=D("450"), gst_percentage=D("0"),
                                                vat_class=spirits)
        Recipe.objects.create(menu_item=self.cocktail, inventory_item=self.rum, quantity_required=D("60"), unit="ml")
        self.beer = self.stock("Kingfisher bottle", "pcs", "48", cost="90", category="Bar")
        self.paneer = self.stock("Paneer", "kg", "5", cost="400")
        fries = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food, name="Paneer Tikka",
                                        price=D("250"), gst_percentage=D("5"))
        Recipe.objects.create(menu_item=fries, inventory_item=self.paneer, quantity_required=D("0.2"), unit="kg")

    def stock(self, name, unit, qty, cost="0", bottle=None, category=""):
        return InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name=name, unit=unit,
                                            stock=D(qty), cost_price=D(cost), bottle_ml=bottle, category=category)

    def sell_pegs(self, n):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.manager,
                                     source="dine_in", status="open")
        add_items_to_order(self.manager, order, [{"id": self.peg.id, "quantity": n}])
        deduct_inventory_for_items(list(order.items.all()))     # what sending the ticket does

    def rum_stock(self):
        self.rum.refresh_from_db()
        return self.rum.stock


class FreePourTest(Base):
    def test_two_free_pegs_are_invisible_to_the_till_and_caught_by_the_count(self):
        self.sell_pegs(10)                               # 300 ml rung up
        # Two free pegs (60 ml) poured: nothing in Rasova moves, so the
        # till still says 7,200 ml are left.
        self.assertEqual(self.rum_stock(), D("7200"))
        # The bottles hold 7,140 ml: 9 sealed + 390 ml in the open one.
        count = save_count(self.manager, self.outlet, {self.rum.id: ("9", "390")})
        report = count_report(count)
        row = report["rows"][0]
        self.assertEqual((row["expected"], row["counted"]), (D("7200"), D("7140")))
        self.assertEqual((row["missing"], row["missing_pegs"]), (D("60"), D("2.0")))
        # Priced at the cheapest way it sells: the Rs 450 punch takes 60 ml
        # (Rs 7.50 a ml), under the Rs 300 peg's Rs 10 a ml.
        self.assertEqual(row["at_price"], D("450.00"))
        self.assertEqual(row["at_cost"], D("72.00"))
        self.assertEqual((report["missing_pegs"], report["missing_at_price"]), (D("2.0"), D("450.00")))

    def test_the_stock_is_set_to_the_count_with_a_reason(self):
        self.sell_pegs(10)
        save_count(self.manager, self.outlet, {self.rum.id: ("9", "390")})
        self.assertEqual(self.rum_stock(), D("7140"))
        txn = InventoryTransaction.objects.get(item=self.rum, transaction_type="adjustment")
        self.assertEqual(txn.quantity, D("-60"))
        self.assertTrue(txn.reference.startswith("Bar count "))


class CountRulesTest(Base):
    def test_bar_stock_is_what_liquor_uses_and_what_is_filed_under_bar(self):
        self.assertEqual([i.name for i in bar_items(self.tenant, self.outlet)], ["Old Monk", "Kingfisher bottle"])

    def test_bottles_are_counted_in_pieces(self):
        count = save_count(self.manager, self.outlet, {self.beer.id: ("45", "")})
        self.assertEqual(count_report(count)["rows"][0]["missing"], D("3"))

    def test_blank_bottles_are_skipped_and_an_empty_count_is_refused(self):
        count = save_count(self.manager, self.outlet, {self.rum.id: ("10", "0"), self.beer.id: ("", "")})
        self.assertEqual(count.lines.count(), 1)
        with self.assertRaisesMessage(CountError, "Count at least one bottle."):
            save_count(self.manager, self.outlet, {self.rum.id: ("", ""), self.beer.id: ("", "")})

    def test_sealed_bottles_need_a_bottle_size(self):
        InventoryItem.objects.filter(id=self.rum.id).update(bottle_ml=None)
        with self.assertRaisesMessage(CountError, "Set the bottle size of Old Monk first"):
            save_count(self.manager, self.outlet, {self.rum.id: ("9", "390")})
        self.assertFalse(BarCount.objects.exists())
        self.assertEqual(self.rum_stock(), D("7500"))
        # Without a size, the total ml is typed.
        save_count(self.manager, self.outlet, {self.rum.id: ("", "7400")})
        self.assertEqual(self.rum_stock(), D("7400"))

    def test_a_bad_figure_saves_nothing(self):
        for bad in (("-1", ""), ("1.5", ""), ("", "abc")):
            with self.assertRaises(CountError):
                save_count(self.manager, self.outlet, {self.rum.id: bad})
        self.assertFalse(BarCount.objects.exists())

    def test_the_cheapest_way_it_sells_prices_the_gap(self):
        self.assertEqual(sell_prices([self.rum])[self.rum.id], D("7.5"))


class CountScreensTest(Base):
    def client_for(self, user):
        client = Client()
        client.force_login(user)
        return client

    def test_the_count_is_blind(self):
        page = self.client_for(self.manager).get(reverse("bar_count"))
        self.assertContains(page, "Old Monk")
        self.assertContains(page, "750 ml bottle")
        self.assertNotContains(page, "7500")
        self.assertNotContains(page, "Paneer")

    def test_saving_shows_the_result(self):
        # 9 sealed + 720 ml open = 7,470 ml of the 7,500 expected: one peg short.
        resp = self.client_for(self.manager).post(reverse("bar_count"), {
            f"bottles_{self.rum.id}": "9", f"amount_{self.rum.id}": "720"}, follow=True)
        self.assertContains(resp, "Pegs missing")
        self.assertContains(resp, '<div class="summary-num bad" id="missingPegs">1.0</div>', html=True)
        count = BarCount.objects.get()
        self.assertEqual(count.counted_by, self.manager)

    def test_a_cashier_cannot_count_and_another_outlet_cannot_see(self):
        self.assertNotEqual(self.client_for(self.cashier).get(reverse("bar_count")).status_code, 200)
        count = save_count(self.manager, self.outlet, {self.rum.id: ("10", "0")})
        other = Outlet.objects.create(tenant=self.tenant, name="Other")
        stranger = User.objects.create_user(username="count_other", password="pw", role="manager",
                                            tenant=self.tenant, outlet=other)
        self.assertEqual(self.client_for(stranger).get(reverse("bar_count_result", args=[count.id])).status_code, 404)

    def test_past_counts_list_them(self):
        save_count(self.manager, self.outlet, {self.rum.id: ("9", "690")})
        page = self.client_for(self.manager).get(reverse("bar_count_list"))
        self.assertContains(page, "count_mgr")
        self.assertContains(page, "2.0")


class CountQueriesTest(Base):
    """Saving a count and listing past counts cost the same number of
    queries for one bottle as for many."""

    def queries(self, fn):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return len(ctx.captured_queries)

    def test_saving_is_not_a_query_per_bottle(self):
        one = self.queries(lambda: save_count(self.manager, self.outlet, {self.rum.id: ("9", "390")}))
        bottles = [self.stock(f"Gin {n}", "ml", "750", cost="2", bottle=750, category="Bar") for n in range(5)]
        entries = {b.id: ("0", "700") for b in bottles}
        entries[self.rum.id] = ("9", "300")
        self.assertEqual(self.queries(lambda: save_count(self.manager, self.outlet, entries)), one)

    def test_past_counts_are_not_a_query_per_count(self):
        client = Client()
        client.force_login(self.manager)
        save_count(self.manager, self.outlet, {self.rum.id: ("9", "390")})
        one = self.queries(lambda: client.get(reverse("bar_count_list")))
        for _ in range(4):
            save_count(self.manager, self.outlet, {self.rum.id: ("9", "390"), self.beer.id: ("40", "")})
        self.assertEqual(self.queries(lambda: client.get(reverse("bar_count_list"))), one)
