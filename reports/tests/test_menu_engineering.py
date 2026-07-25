# reports/tests/test_menu_engineering.py
"""
Hand-calculated regression tests for menu_engineering_report().

Run: python manage.py test reports.tests.test_menu_engineering
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date
from inventory.models import InventoryItem, Recipe
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from tenants.models import Outlet, Tenant


class MenuEngineeringQuadrantTest(TestCase):
    """
    5 items, by hand:

      Star      -- qty 10, revenue 1000, cogs 200  -> margin 80%
      Plowhorse -- qty 10, revenue 1000, cogs 800  -> margin 20%
      Puzzle    -- qty 2,  revenue 200,  cogs 20   -> margin 90%
      Dog       -- qty 2,  revenue 200,  cogs 180  -> margin 10%
      Unknown   -- qty 100, revenue 100, NO recipe -> cost unknown

    median_qty (all 5 items, popularity doesn't need cost data):
      sorted [2, 2, 10, 10, 100] -> middle value = 10

    median_margin_pct (ONLY the 4 cost-known items -- the unknown item must
    not drag this axis):
      sorted [10, 20, 80, 90] -> average of two middle values = 50.0

    Quadrant = (qty >= 10, margin >= 50):
      Star:      (True,  True)  -> Star
      Plowhorse: (True,  False) -> Plowhorse
      Puzzle:    (False, True)  -> Puzzle
      Dog:       (False, False) -> Dog
      Unknown:   cost unknown   -> Unknown (never compared against the axes)
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Menu Eng Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.user = User.objects.create_user(
            username="menueng_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains",
        )
        # Must match get_business_date(), not plain timezone.localdate() --
        # menu_engineering_report uses get_business_date_range() to bound the
        # query, which is business-day-cutoff-aware, not calendar-date-aware.
        # Near a cutoff hour the two disagree on what "today" is.
        self.today = get_business_date(timezone.now(), self.outlet)
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, created_by=self.user, status="paid",
        )

        def _make_item(name, qty, price, cost_price=None, recipe_qty=None):
            item = MenuItem.objects.create(
                tenant=self.tenant, outlet=self.outlet, category=self.category,
                name=name, price=Decimal(str(price)), gst_percentage=Decimal("0"),
            )
            if cost_price is not None:
                inv = InventoryItem.objects.create(
                    tenant=self.tenant, outlet=self.outlet, name=f"{name} ingredient",
                    unit="g", cost_price=Decimal(str(cost_price)),
                )
                Recipe.objects.create(
                    menu_item=item, inventory_item=inv,
                    quantity_required=Decimal(str(recipe_qty)), unit="g",
                )
            OrderItem.objects.create(
                order=self.order, menu_item=item, quantity=qty,
                price=Decimal(str(price)), gst_percentage=Decimal("0"),
                total_price=Decimal(str(qty * price)), status="pending",
            )
            return item

        self.star      = _make_item("Star Dish",      qty=10, price=100, cost_price=20, recipe_qty=1)   # cogs=200
        self.plowhorse = _make_item("Plowhorse Dish",  qty=10, price=100, cost_price=80, recipe_qty=1)   # cogs=800
        self.puzzle    = _make_item("Puzzle Dish",     qty=2,  price=100, cost_price=10, recipe_qty=1)   # cogs=20
        self.dog       = _make_item("Dog Dish",        qty=2,  price=100, cost_price=90, recipe_qty=1)   # cogs=180
        self.unknown   = _make_item("Mystery Dish",    qty=100, price=1)                                  # no recipe

    def _report(self):
        from reports.services.menu_engineering import menu_engineering_report
        return menu_engineering_report(self.tenant, self.outlet, self.today, self.today)

    def _by_name(self, report, name):
        return next(i for i in report["items"] if i["name"] == name)

    def test_median_qty_includes_the_unknown_cost_item(self):
        # Popularity doesn't need cost data -- the unknown-cost item's qty
        # (100) must still participate, pulling the median from 6 (the
        # 4-known-item median) up to 10.
        report = self._report()
        self.assertEqual(report["median_qty"], 10)

    def test_median_margin_excludes_the_unknown_cost_item(self):
        # If the unknown-cost item leaked into this axis under any fallback
        # value, this median would almost certainly not land on exactly 50.0.
        report = self._report()
        self.assertEqual(report["median_margin_pct"], 50.0)

    def test_star_quadrant(self):
        item = self._by_name(self._report(), "Star Dish")
        self.assertEqual(item["margin_pct"], 80.0)
        self.assertEqual(item["quadrant"], "Star")

    def test_plowhorse_quadrant(self):
        item = self._by_name(self._report(), "Plowhorse Dish")
        self.assertEqual(item["margin_pct"], 20.0)
        self.assertEqual(item["quadrant"], "Plowhorse")

    def test_puzzle_quadrant(self):
        item = self._by_name(self._report(), "Puzzle Dish")
        self.assertEqual(item["margin_pct"], 90.0)
        self.assertEqual(item["quadrant"], "Puzzle")

    def test_dog_quadrant(self):
        item = self._by_name(self._report(), "Dog Dish")
        self.assertEqual(item["margin_pct"], 10.0)
        self.assertEqual(item["quadrant"], "Dog")

    def test_unknown_cost_item_is_never_classified(self):
        item = self._by_name(self._report(), "Mystery Dish")
        self.assertFalse(item["cogs_known"])
        self.assertIsNone(item["cogs"])
        self.assertIsNone(item["margin_pct"])
        self.assertEqual(item["quadrant"], "Unknown")

    def test_items_with_unknown_cost_count(self):
        report = self._report()
        self.assertEqual(report["items_with_unknown_cost"], 1)

    def test_no_cap_on_item_count(self):
        # top_items() caps at 10; menu engineering deliberately doesn't --
        # the whole point is seeing the full menu.
        report = self._report()
        self.assertEqual(len(report["items"]), 5)
