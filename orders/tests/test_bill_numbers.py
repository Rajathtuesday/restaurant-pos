"""
Bill numbers (P18): SG/2627/000123, one series per outlet per financial year,
at most 16 characters (CGST Rules, rule 46(b)). orders/services/bill_numbers.py
sets out the rules; these pin each one:

  * a number is given the first time an order is billed, never when it is
    opened, so an order cancelled before its bill leaves no gap
  * each outlet has its own series, and each financial year starts again
  * a bill keeps its number, even when a screen holding an older copy saves
    it, and two screens billing at once still give one number
  * bills from before this keep the number they were printed with, and every
    existing outlet gets a code (the migrations, run on real rows)

Run: python manage.py test orders.tests.test_bill_numbers
"""
import datetime as dt
import re
import threading
from decimal import Decimal as D
from importlib import import_module
from unittest import skipUnless

from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date
from menu.models import MenuCategory, MenuItem
from orders.models import BillSeries, Order, OrderItem
from orders.services.demo_seed import create_or_reset_demo_tenant
from orders.services.bill_numbers import financial_year
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant, suggest_bill_code

NUMBER = re.compile(r"^[A-Z0-9]{1,3}/\d{4}/\d{6,7}$")


class RulesTest(SimpleTestCase):

    def test_the_financial_year_runs_april_to_march(self):
        self.assertEqual(financial_year(dt.date(2026, 4, 1)), "2627")
        self.assertEqual(financial_year(dt.date(2026, 9, 28)), "2627")
        self.assertEqual(financial_year(dt.date(2027, 3, 31)), "2627")
        self.assertEqual(financial_year(dt.date(2027, 4, 1)), "2728")
        self.assertEqual(financial_year(dt.date(2099, 4, 1)), "9900")

    def test_a_code_comes_from_the_restaurants_name(self):
        self.assertEqual(suggest_bill_code("Spice Garden"), "SG")
        self.assertEqual(suggest_bill_code("Malenadu"), "MAL")
        self.assertEqual(suggest_bill_code("The Big Bangalore Brewery"), "TBB")
        self.assertEqual(suggest_bill_code("!!!"), "R")

    def test_sister_outlets_get_a_digit(self):
        self.assertEqual(suggest_bill_code("Spice Garden", {"SG"}), "SG2")
        self.assertEqual(suggest_bill_code("Spice Garden", {"SG", "SG2"}), "SG3")
        self.assertEqual(suggest_bill_code("Malenadu", {"MAL"}), "MA2")


class BillWorld(TestCase):
    """Spice Garden: an outlet with a GSTIN, a table and a ₹100 dosa at 5%."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Spice Garden")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Indiranagar", gst_no=SAMPLE_GSTIN)
        self.owner = User.objects.create_user(username="bill_no_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Tiffin")
        self.dosa = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                            name="Dosa", price=D("100"), gst_percentage=D("5"))
        self.year = financial_year(get_business_date(timezone.now(), self.outlet))

    def order(self, outlet=None, **fields):
        outlet = outlet or self.outlet
        order = Order.objects.create(tenant=self.tenant, outlet=outlet, created_by=self.owner,
                                     source="counter", **fields)
        OrderItem.objects.create(order=order, menu_item=self.dosa, quantity=1, price=D("100"),
                                 gst_percentage=D("5"), total_price=D("100"))
        return order

    def bill(self, order, status="billing"):
        """The Bill button (billing) or a settled payment (closed): the save
        that bills the order."""
        order.status = status
        order.save(update_fields=["status"])
        return order.bill_number


class NumberingTest(BillWorld):

    def test_an_outlet_gets_its_code_when_it_is_made(self):
        second = Outlet.objects.create(tenant=self.tenant, name="Koramangala")
        self.assertEqual((self.outlet.bill_code, second.bill_code), ("SG", "SG2"))

    def test_a_number_is_given_when_the_order_is_billed_not_when_it_is_opened(self):
        order = self.order()
        self.assertIsNone(order.bill_number)
        number = self.bill(order)
        self.assertEqual(number, f"SG/{self.year}/000001")
        self.assertLessEqual(len(number), 16)
        self.assertRegex(number, NUMBER)
        self.assertEqual(Order.objects.get(pk=order.pk).bill_number, number)

    def test_the_series_counts_up_with_no_gap_for_orders_cancelled_before_billing(self):
        first = self.bill(self.order())
        cancelled = self.order()
        cancelled.status = "cancelled"
        cancelled.save(update_fields=["status"])
        second = self.bill(self.order(), status="closed")
        self.assertEqual((first[-6:], second[-6:]), ("000001", "000002"))
        self.assertIsNone(Order.objects.get(pk=cancelled.pk).bill_number)

    def test_a_bill_keeps_its_number_from_billing_to_payment(self):
        order = self.order()
        number = self.bill(order)
        order.status = "open"                   # more dishes after the bill was shown
        order.save(update_fields=["status"])
        self.assertEqual(self.bill(order, status="closed"), number)
        self.assertEqual(BillSeries.objects.get(outlet=self.outlet).last_number, 1)

    def test_an_order_that_arrives_paid_is_numbered_at_once(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, source="swiggy", status="paid")
        self.assertEqual(order.bill_number, f"SG/{self.year}/000001")

    def test_each_outlet_has_its_own_series(self):
        koramangala = Outlet.objects.create(tenant=self.tenant, name="Koramangala", gst_no=SAMPLE_GSTIN)
        self.assertEqual(self.bill(self.order()), f"SG/{self.year}/000001")
        self.assertEqual(self.bill(self.order(koramangala)), f"SG2/{self.year}/000001")
        self.assertEqual(self.bill(self.order()), f"SG/{self.year}/000002")

    def test_each_financial_year_starts_again(self):
        def opened_on(day):
            order = self.order()
            moment = timezone.make_aware(dt.datetime.combine(day, dt.time(20, 0)))
            Order.objects.filter(pk=order.pk).update(created_at=moment)
            return Order.objects.get(pk=order.pk)

        self.assertEqual(self.bill(opened_on(dt.date(2027, 3, 31))), "SG/2627/000001")
        self.assertEqual(self.bill(opened_on(dt.date(2027, 4, 1))), "SG/2728/000001")
        self.assertEqual(self.bill(opened_on(dt.date(2027, 3, 30))), "SG/2627/000002")

    def test_a_copy_read_before_the_bill_never_wipes_its_number(self):
        order = self.order()
        stale = Order.objects.get(pk=order.pk)            # another screen's older copy
        number = self.bill(order)
        stale.customer_name = "Asha"
        stale.save()                                      # a full save of the older copy
        self.assertEqual(Order.objects.get(pk=order.pk).bill_number, number)

    def test_an_older_copy_billing_again_gets_the_same_number(self):
        order = self.order()
        stale = Order.objects.get(pk=order.pk)
        number = self.bill(order)
        stale.status = "closed"
        stale.save()
        self.assertEqual((stale.bill_number, Order.objects.get(pk=order.pk).bill_number), (number, number))
        self.assertEqual(BillSeries.objects.get(outlet=self.outlet).last_number, 1)


class PrintedNumberTest(BillWorld):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.owner)

    def test_the_bill_and_the_receipt_show_the_bill_number(self):
        order = self.order()
        number = self.bill(order)
        self.assertContains(self.client.get(reverse("bill-view", args=[order.id])), number)
        self.assertContains(self.client.get(reverse("thermal-receipt", args=[order.id])), number)

    def test_before_billing_the_page_shows_the_order_number(self):
        order = self.order()
        self.assertContains(self.client.get(reverse("bill-view", args=[order.id])), order.order_number)


class BillCodeSettingTest(BillWorld):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.owner)

    def save_code(self, code):
        return self.client.post(reverse("outlet_settings"), {
            "outlet_name": "Indiranagar", "gst_no": SAMPLE_GSTIN, "gst_inclusive": "false", "bill_code": code,
        }, follow=True)

    def test_the_page_shows_what_a_bill_number_will_look_like(self):
        self.assertContains(self.client.get(reverse("outlet_settings")), f"SG/{self.year}/000001")

    def test_a_new_code_starts_a_new_series(self):
        self.bill(self.order())
        self.save_code("ind")
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.bill_code, "IND")
        self.assertEqual(self.bill(self.order()), f"IND/{self.year}/000001")

    def test_a_code_that_cant_be_used_is_refused_with_the_reason(self):
        self.assertContains(self.save_code("S/G"), "S/G can&#x27;t start bill numbers.")
        Outlet.objects.create(tenant=self.tenant, name="Koramangala")          # SG2
        self.assertContains(self.save_code("SG2"), "SG2 already starts the bills of Koramangala")
        self.save_code("")
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.bill_code, "SG")


class DemoResetTest(TestCase):

    def test_each_reset_starts_the_demo_series_again(self):
        for _ in range(2):
            tenant = create_or_reset_demo_tenant()
            numbers = sorted(Order.objects.filter(tenant=tenant).exclude(bill_number=None)
                             .values_list("bill_number", flat=True))
            self.assertTrue(numbers)
            self.assertTrue(numbers[0].endswith("/000001"), numbers[0])


@skipUnless(connection.vendor == "postgresql", "row locks are tested on Postgres")
class ConcurrentBillingTest(TransactionTestCase):
    """Screens billing at the same moment, on real row locks."""

    def setUp(self):
        BillWorld.setUp(self)

    order = BillWorld.order

    def _all_at_once(self, jobs):
        start = threading.Barrier(len(jobs))
        errors = []

        def run(job):
            try:
                start.wait()
                job()
            except Exception as exc:          # reported below, not lost in the thread
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(job,)) for job in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])

    def test_bills_billed_together_get_consecutive_numbers(self):
        orders = [self.order() for _ in range(8)]

        def bill(order_id):
            def job():
                order = Order.objects.get(pk=order_id)
                order.status = "billing"
                order.save(update_fields=["status"])
            return job

        self._all_at_once([bill(order.pk) for order in orders])
        numbers = sorted(Order.objects.filter(pk__in=[o.pk for o in orders]).values_list("bill_number", flat=True))
        self.assertEqual(numbers, [f"SG/{self.year}/{n:06d}" for n in range(1, 9)])

    def test_one_bill_billed_on_two_screens_gets_one_number(self):
        order = self.order()

        def bill():
            copy = Order.objects.get(pk=order.pk)
            copy.status = "billing"
            copy.save(update_fields=["status"])

        self._all_at_once([bill, bill])
        self.assertEqual(Order.objects.get(pk=order.pk).bill_number, f"SG/{self.year}/000001")
        self.assertEqual(BillSeries.objects.get(outlet=self.outlet).last_number, 1)


class MigrationTest(TransactionTestCase):
    """The two migrations, run on rows as they are in production today."""

    before = [("tenants", "0036_demo_outlet_sample_gstin"), ("orders", "0063_liquor_vat")]
    after = [("tenants", "0037_outlet_bill_code"), ("orders", "0064_bill_numbers")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())       # back to today's schema

    def test_old_bills_keep_their_numbers_and_every_outlet_gets_a_code(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old = executor.loader.project_state(self.before).apps
        Tenant_, Outlet_, Order_ = (old.get_model(*name) for name in
                                    [("tenants", "Tenant"), ("tenants", "Outlet"), ("orders", "Order")])
        tenant = Tenant_.objects.create(name="Spice Garden", slug="spice-garden-mig")
        main = Outlet_.objects.create(tenant=tenant, name="Main", outlet_number=1)
        second = Outlet_.objects.create(tenant=tenant, name="Second", outlet_number=2)
        paid = Order_.objects.create(tenant=tenant, outlet=main, status="closed", order_number="INV-7-20260927-0003")
        shown = Order_.objects.create(tenant=tenant, outlet=main, status="billing", order_number="INV-7-20260928-0001")
        still_open = Order_.objects.create(tenant=tenant, outlet=main, status="open", order_number="INV-7-20260928-0002")

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        new = executor.loader.project_state(self.after).apps
        Outlet_, Order_ = new.get_model("tenants", "Outlet"), new.get_model("orders", "Order")

        self.assertEqual(Order_.objects.get(pk=paid.pk).bill_number, "INV-7-20260927-0003")
        self.assertEqual(Order_.objects.get(pk=shown.pk).bill_number, "INV-7-20260928-0001")
        self.assertIsNone(Order_.objects.get(pk=still_open.pk).bill_number)
        self.assertEqual(sorted(Outlet_.objects.filter(pk__in=[main.pk, second.pk]).values_list("bill_code", flat=True)),
                         ["SG", "SG2"])

    def test_the_migrations_frozen_code_rule_matches_the_apps(self):
        frozen = import_module("tenants.migrations.0037_outlet_bill_code")._suggest
        for name, taken in [("Spice Garden", set()), ("Malenadu", {"MAL"}), ("A", {"A", "A2"}), ("", set())]:
            self.assertEqual(frozen(name, taken), suggest_bill_code(name, taken))
        self.assertIsNotNone(django_apps.get_model("orders", "BillSeries"))
