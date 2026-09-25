"""
The inventory report shows amounts used, wasted and spent as positive numbers.

The stock ledger stores stock going out as negative ("consume" -2 kg), which
made the report's Consumption Cost card read "Rs -1,234.00", listed usage as
negative, and sorted the least-used and cheapest items first. The report
services now flip the sign once, leave out items that net to zero, and sort
the biggest first. The ledger tab keeps its signs (+ in, - out).

Run: python manage.py test reports.tests.test_inventory_report_amounts
"""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import InventoryItem, InventoryTransaction
from reports.services.inventory_reports import inventory_cost, inventory_usage, inventory_wastage
from reports.tests.test_reports import create_base_fixtures


class InventoryReportAmountsTest(TestCase):

    def setUp(self):
        self.tenant, self.outlet, self.user, _ = create_base_fixtures()
        self.day = timezone.localdate()
        self.rice = self._item("Basmati Rice", "120")
        self.paneer = self._item("Paneer", "400")
        self.oil = self._item("Oil", "150")
        self._move(self.rice, "consume", "-2.000")
        self._move(self.oil, "consume", "-0.400")
        self._move(self.oil, "consume", "-0.200")
        self._move(self.paneer, "consume", "-0.500")
        self._move(self.paneer, "consume", "0.500")     # a dish cancelled and put back
        self._move(self.rice, "wastage", "-0.250")
        self._move(self.oil, "wastage", "-0.100")

    def _item(self, name, cost):
        return InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name=name, unit="kg",
            stock=Decimal("10"), cost_price=Decimal(cost),
        )

    def _move(self, item, kind, qty):
        InventoryTransaction.objects.create(
            tenant=self.tenant, outlet=self.outlet, item=item, transaction_type=kind, quantity=Decimal(qty),
        )

    def test_usage_is_positive_and_most_used_first(self):
        rows = inventory_usage(self.tenant, self.outlet, self.day, self.day)
        self.assertEqual([(r["item__name"], r["total_qty"]) for r in rows],
                         [("Basmati Rice", Decimal("2.000")), ("Oil", Decimal("0.600"))])

    def test_an_item_that_nets_to_zero_is_left_out(self):
        names = [r["item__name"] for r in inventory_usage(self.tenant, self.outlet, self.day, self.day)]
        self.assertNotIn("Paneer", names)

    def test_cost_is_positive_and_dearest_first(self):
        rows = inventory_cost(self.tenant, self.outlet, self.day, self.day)
        self.assertEqual([(r["item__name"], r["total_cost"]) for r in rows],
                         [("Basmati Rice", Decimal("240.000")), ("Oil", Decimal("90.000"))])

    def test_wastage_is_positive_and_most_wasted_first(self):
        rows = inventory_wastage(self.tenant, self.outlet, self.day, self.day)
        self.assertEqual([(r["item__name"], r["total_qty"], r["total_cost"]) for r in rows],
                         [("Basmati Rice", Decimal("0.250"), Decimal("30.000")),
                          ("Oil", Decimal("0.100"), Decimal("15.000"))])

    def test_page_shows_no_negative_money(self):
        client = Client()
        client.force_login(self.user)
        resp = client.get(reverse("inventory_report"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_cost"], Decimal("330.000"))
        self.assertEqual(resp.context["total_wastage_cost"], Decimal("45.000"))
        self.assertEqual(len(resp.context["usage"]), 2)    # the "Items Consumed" card
        self.assertNotContains(resp, "₹-")
        self.assertContains(resp, "₹330.00")

    def test_ledger_keeps_its_signs(self):
        client = Client()
        client.force_login(self.user)
        resp = client.get(reverse("inventory_report"))
        self.assertContains(resp, "-2.000")      # stock out
        self.assertContains(resp, "+0.500")      # stock back in

    def test_tab_row_scrolls_on_a_phone_and_centres_the_current_tab(self):
        client = Client()
        client.force_login(self.user)
        page = client.get(reverse("inventory_report")).content.decode()
        self.assertIn('class="report-subnav-left"', page)
        self.assertIn('strip.querySelector(".report-tab.active")', page)
