"""
Every report counts a day the same way: the business day, 6 AM to 6 AM (P16).

A restaurant open past midnight sells at 12:30 AM on the day still trading.
The profit and comparison reports, the stock consumption and variance
reports, the daily chart and order history used to count calendar days, so
they put those sales on the next day and disagreed with daily sales and the
GST return. These tests stand at 1 AM on 29 September, with a sale at 10 PM
on the 28th, one at 12:30 AM and one at 7 AM on the 29th, and check that
every report puts the first two on the 28th and the third on the 29th.

Run: python manage.py test reports.tests.test_business_day
"""
import csv
import datetime as dt
import io
import zoneinfo
from decimal import Decimal as D
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import InventoryItem, InventoryTransaction, Recipe
from orders.models import Order, OrderItem, Payment
from reports.services.comparison_reports import period_comparison
from reports.services.pl_reports import gross_margin_report
from reports.services.sales_reports import daily_sales, hourly_sales
from reports.tests.test_reports import create_base_fixtures

IST = zoneinfo.ZoneInfo("Asia/Kolkata")
DAY_28, DAY_29 = dt.date(2026, 9, 28), dt.date(2026, 9, 29)


def at(day, hour, minute=0):
    return dt.datetime(2026, 9, day, hour, minute, tzinfo=IST)


class NightSalesTest(TestCase):

    def setUp(self):
        self.tenant, self.outlet, self.owner, self.paneer = create_base_fixtures()   # Paneer Tikka, ₹200
        self.waiter = User.objects.create_user(username="night_waiter", password="pw", role="waiter",
                                               tenant=self.tenant, outlet=self.outlet)
        self.cheese = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name="Cheese", unit="kg",
                                                   stock=D("10"), cost_price=D("400"))
        Recipe.objects.create(menu_item=self.paneer, inventory_item=self.cheese,
                              quantity_required=D("0.100"), unit="kg")
        self.evening = self.sale(at(28, 22), at(28, 22, 40), 1)
        self.night = self.sale(at(29, 0, 30), at(29, 0, 50), 2)
        self.morning = self.sale(at(29, 7), at(29, 7, 30), 4)
        used = InventoryTransaction.objects.create(tenant=self.tenant, outlet=self.outlet, item=self.cheese,
                                                   transaction_type="consume", quantity=D("-0.300"))
        InventoryTransaction.objects.filter(pk=used.pk).update(created_at=at(29, 0, 45))

    def sale(self, opened, paid, quantity):
        """Opened, served and paid at the given times, by the waiter."""
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.waiter,
                                     source="dine_in", status="open")
        OrderItem.objects.create(order=order, menu_item=self.paneer, quantity=quantity, price=D("200"),
                                 gst_percentage=D("5"), total_price=D("200") * quantity, status="served")
        order.recalculate_totals()
        order.created_at = opened
        Order.objects.filter(pk=order.pk).update(created_at=opened)
        order.status = "closed"
        order.save(update_fields=["status"])
        payment = Payment.objects.create(order=order, method="cash", amount=order.grand_total)
        Payment.objects.filter(pk=payment.pk).update(paid_at=paid)
        return order

    def csv_rows(self, url_name, day):
        client = Client()
        client.force_login(self.owner)
        response = client.get(reverse(url_name), {"date": day.isoformat(), "export": "csv"})
        return {row[next(iter(row))]: row for row in csv.DictReader(io.StringIO(response.content.decode()))}

    def test_profit_comparison_and_daily_sales_agree_on_the_night(self):
        margin = gross_margin_report(self.tenant, self.outlet, DAY_28, DAY_28)
        compared = period_comparison(self.tenant, self.outlet, DAY_28, DAY_28)["current"]
        sales = daily_sales(self.tenant, self.outlet, DAY_28, DAY_28)
        # 1 + 2 dishes at ₹200 on the 28th: ₹600 in all three
        self.assertEqual((margin["order_count"], margin["gross_revenue"]), (2, 600.0))
        self.assertEqual((compared["orders"], compared["revenue"]), (2, 600.0))
        self.assertEqual((sales["orders"], sales["total_sales"]), (2, 600.0))
        self.assertEqual(gross_margin_report(self.tenant, self.outlet, DAY_29, DAY_29)["order_count"], 1)

    def test_the_daily_chart_puts_the_night_on_its_business_day(self):
        chart = hourly_sales(self.tenant, self.outlet, DAY_28, DAY_29)
        self.assertEqual(chart, [{"label": "Sep 28", "total": 600.0}, {"label": "Sep 29", "total": 800.0}])

    def test_stock_reports_count_the_night_on_its_business_day(self):
        # 3 paneer sold on the 28th, 0.1 kg of cheese each; 4 on the 29th
        self.assertEqual(self.csv_rows("inventory_consumption", DAY_28)["Cheese"]["Consumed Today"], "0.300")
        self.assertEqual(self.csv_rows("inventory_consumption", DAY_29)["Cheese"]["Consumed Today"], "0.400")
        # the 12:45 AM stock use is the 28th's
        self.assertEqual(self.csv_rows("inventory_variance", DAY_28)["Cheese"]["Txn Consumed"], "0.300")
        self.assertEqual(self.csv_rows("inventory_variance", DAY_29)["Cheese"]["Txn Consumed"], "0.000")

    def test_a_waiter_at_1_am_still_sees_the_evenings_bills(self):
        client = Client()
        client.force_login(self.waiter)
        with mock.patch("django.utils.timezone.now", return_value=at(29, 1)):
            page = client.get(reverse("order-history")).content.decode()
        self.assertIn(self.evening.display_number, page)
        self.assertIn(self.night.display_number, page)
        self.assertNotIn(self.morning.display_number, page)
