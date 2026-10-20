"""
Changing a keg (inventory/kegs.py), 20 Oct 2026.

A 30 litre keg that sells 26 litres through the till lost 4 litres to foam,
spillage, or pints that left without a bill. Until now Rasova kept those
4 litres in stock forever, so the stock and the keg's real yield were both
wrong. Pressing "Change keg" now records what the finished keg sold and
lost, and takes the loss off the stock.

Run: python manage.py test inventory.tests.test_kegs
"""
from datetime import timedelta
from decimal import Decimal as D

from django.db import connection
from django.db.models import F
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import User
from inventory.kegs import KegError, change_keg, keg_board, keg_items
from inventory.models import InventoryItem, InventoryTransaction, KegChange, Recipe
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from orders.services.inventory_service import deduct_inventory_for_items
from orders.services.order_service import add_items_to_order
from tenants.models import Outlet, Tenant, TenantFeatureOverride


class Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tap House", tenant_type="pub")
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no="29ABCDE1234F1Z5",
                                            liquor_hours_enforced=False)
        self.manager = User.objects.create_user(username="tap_mgr", password="pw", role="manager",
                                                tenant=self.tenant, outlet=self.outlet)
        beer = add_liquor_class(self.outlet, "Beer")
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        # Two kegs in the cellar plus the one on tap: 90 litres.
        self.draught = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name="Lager draught",
                                                    unit="ml", stock=D("90000"), cost_price=D("0.20"),
                                                    bottle_ml=30000)
        self.pint = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=bar, name="Lager pint",
                                            price=D("250"), gst_percentage=D("0"), vat_class=beer)
        Recipe.objects.create(menu_item=self.pint, inventory_item=self.draught, quantity_required=D("500"), unit="ml")
        self.wine = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name="House red", unit="ml",
                                                 stock=D("7500"), bottle_ml=750, category="Bar")

    def sell(self, pints):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.manager,
                                     source="dine_in", status="open")
        add_items_to_order(self.manager, order, [{"id": self.pint.id, "quantity": pints}])
        deduct_inventory_for_items(list(order.items.all()))
        return order

    def stock(self):
        self.draught.refresh_from_db()
        return self.draught.stock

    def change(self, item=None):
        """Change a keg, as if the last change was an hour ago (two within
        DOUBLE_PRESS are refused as a double tap)."""
        KegChange.objects.update(changed_at=F("changed_at") - timedelta(hours=1))
        return change_keg(self.manager, self.outlet, (item or self.draught).id)


class KegChangeTest(Base):
    def test_only_big_containers_are_kegs(self):
        self.assertEqual([i.name for i in keg_items(self.tenant, self.outlet)], ["Lager draught"])

    def test_the_first_change_only_starts_the_count(self):
        self.sell(10)
        change = self.change()
        self.assertEqual((change.sold, change.lost), (None, None))
        self.assertEqual(self.stock(), D("85000"))

    def test_a_keg_that_sold_26_litres_lost_4_and_the_stock_follows(self):
        self.change()      # the keg is tapped
        self.sell(52)                                              # 26 litres of pints
        self.assertEqual(self.stock(), D("64000"))
        change = self.change()
        self.assertEqual((change.sold, change.lost), (D("26000"), D("4000")))
        # Two full kegs are left: the 4 litres the till still counted are gone.
        self.assertEqual(self.stock(), D("60000"))
        txn = InventoryTransaction.objects.get(item=self.draught, transaction_type="adjustment")
        self.assertEqual(txn.quantity, D("-4000"))

    def test_a_cancelled_pint_does_not_count_as_sold(self):
        self.change()
        order = self.sell(52)
        line = order.items.get()
        # The ticket went to the bar; 2 pints sent back unpoured.
        line.status = "sent"
        line.save(update_fields=["status"])
        from orders.services.void_service import reduce_item_quantity
        reduce_item_quantity(self.manager, line.id, 2, "Not poured", made=False)
        change = self.change()
        self.assertEqual(change.sold, D("25000"))

    def test_the_board_shows_the_keg_on_tap_and_each_finished_kegs_yield(self):
        self.change()
        self.sell(52)
        self.change()
        self.sell(30)
        board = keg_board(self.tenant, self.outlet)
        self.assertEqual((board["now"][0]["sold"], board["now"][0]["share"]), (D("15000"), D("50")))
        finished = board["history"][0]
        self.assertEqual((finished["yield"], finished["lost_value"]), (D("87"), D("2000.00")))   # 4 l at Rs 0.50 a ml

    def test_a_bottle_is_not_a_keg(self):
        with self.assertRaises(KegError):
            change_keg(self.manager, self.outlet, self.wine.id)

    def test_a_double_tap_does_not_record_a_whole_keg_lost(self):
        change_keg(self.manager, self.outlet, self.draught.id)
        with self.assertRaisesMessage(KegError, "changed a moment ago"):
            change_keg(self.manager, self.outlet, self.draught.id)
        self.assertEqual(KegChange.objects.count(), 1)
        self.assertEqual(self.stock(), D("90000"))


class KegBoardQueriesTest(Base):
    """The keg page costs the same number of queries for 1 keg or 6."""

    def queries(self):
        with CaptureQueriesContext(connection) as ctx:
            keg_board(self.tenant, self.outlet)
        return len(ctx.captured_queries)

    def test_no_query_per_keg(self):
        self.change()
        self.sell(4)
        one = self.queries()
        for n in range(5):
            keg = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"Keg {n}", unit="ml",
                                               stock=D("30000"), bottle_ml=30000, category="Bar")
            self.change(keg)
        self.assertEqual(self.queries(), one)

    def test_the_page_changes_a_keg(self):
        client = Client()
        client.force_login(self.manager)
        self.assertContains(client.get(reverse("kegs")), "Lager draught")
        client.post(reverse("keg_change", args=[self.draught.id]))
        self.assertEqual(KegChange.objects.count(), 1)


