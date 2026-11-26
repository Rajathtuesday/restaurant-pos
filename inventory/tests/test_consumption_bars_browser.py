"""
The consumption report's usage bars, in a real browser.

Why (26 Nov 2026, item 8 of md_files/rasova_fault_fix_plan_2026-11-26.md):
every USAGE bar was an empty grey track on every screen. The fill is a
<span> with no display, so it was inline and ignored its width and height:
0 px wide while its style said width:92%.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test inventory.tests.test_consumption_bars_browser
"""
from decimal import Decimal

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from inventory.models import InventoryItem, Recipe
from orders.models import Order

BAR = """
const fill = document.querySelector(".bar-fill");
const track = document.querySelector(".bar-wrap");
return { fill: fill.getBoundingClientRect().width, track: track.getBoundingClientRect().width,
         height: fill.getBoundingClientRect().height };
"""


class ConsumptionBarTest(BrowserTestCase):

    def test_a_used_ingredient_fills_its_share_of_the_bar(self):
        r = Restaurant("Alpha", "bar-alpha")
        # The order's 2 dishes use 0.25 kg each: 0.5 kg used, 1.5 kg left, a quarter of the day's 2 kg.
        Recipe.objects.create(menu_item=r.menu_item, inventory_item=r.inventory_item,
                              quantity_required=Decimal("0.25"), unit="kg")
        InventoryItem.objects.filter(pk=r.inventory_item.pk).update(stock=Decimal("1.5"))
        Order.objects.filter(pk=r.order.pk).update(status="closed")
        out = self.run_page("/inventory/consumption/", [("bar", BAR)], user=r.owner)
        self.assertEqual(out["errors"], [])
        bar = out["results"]["bar"]
        self.assertGreater(bar["track"], 0)
        self.assertAlmostEqual(bar["fill"] / bar["track"], 0.25, delta=0.03, msg=bar)
        self.assertGreater(bar["height"], 0, bar)
