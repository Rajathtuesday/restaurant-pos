"""
The demo pub opens on a Friday night already under way (P3): the checklist a
stranger needs to see without anyone in the room.

Run: python manage.py test devtools.tests.test_pub_night
"""
from decimal import Decimal as D
from io import StringIO

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from inventory.kegs import keg_board
from inventory.models import InventoryItem
from notifications.models import Notification
from orders.models import Order, Payment
from reports.services.sales_reports import daily_sales
from shifts.models import CashSession
from tenants.models import Tenant


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PubNightTest(TestCase):

    def setUp(self):
        # The low-stock alert is sent once the night's transaction commits.
        with self.captureOnCommitCallbacks(execute=True):
            call_command("seed_pub_demo", stdout=StringIO())
        self.tenant = Tenant.objects.get(name="Copper Tap (demo)")
        self.outlet = self.tenant.outlets.get()

    def test_tables_are_drinking_and_bills_are_paid(self):
        open_tables = Order.objects.filter(outlet=self.outlet, status="open").values_list("table__name", flat=True)
        self.assertEqual(sorted(open_tables), ["T2", "T4", "T5", "T8"])
        paid = Payment.objects.filter(order__outlet=self.outlet)
        self.assertEqual(paid.count(), 8)
        self.assertEqual({p.method for p in paid}, {"cash", "upi", "card"})
        self.assertTrue(CashSession.objects.filter(outlet=self.outlet, status="open").exists(),
                        "the register is open, so the stranger can take a payment")

    def test_the_bills_carry_liquor_vat(self):
        liquor_bills = [o for o in Order.objects.filter(outlet=self.outlet, status="closed")
                        if any(i.menu_item.vat_class_id for i in o.items.select_related("menu_item"))]
        self.assertGreaterEqual(len(liquor_bills), 6)
        for order in liquor_bills:
            kinds = {row["kind"] for row in order.tax_summary["rows"]}
            self.assertIn("vat", kinds, order.tax_summary)

    def test_the_keg_is_nearly_dry_and_the_alert_went_off(self):
        board = keg_board(self.tenant, self.outlet)
        [lager] = [k for k in board["now"] if k["item"].name == "Lager draught"]
        self.assertGreaterEqual(lager["share"], 80, lager)
        keg = InventoryItem.objects.get(outlet=self.outlet, name="Lager draught")
        self.assertTrue(keg.is_low_stock, keg.stock)
        self.assertTrue(Notification.objects.filter(tenant=self.tenant, type="low_stock").exists())

    def test_tonights_report_has_the_takings(self):
        report = daily_sales(self.tenant, self.outlet)
        paid = sum(p.amount for p in Payment.objects.filter(order__outlet=self.outlet))
        self.assertEqual(D(str(report["total_sales"])).quantize(D("0.01")), paid)
        self.assertEqual(report["orders"], 8)
        self.assertGreater(paid, D("10000"))

    def test_a_night_played_in_the_morning_stays_in_todays_report(self):
        """Seeded at 7:27 am, three hours back is 4:27, before the business
        day began at 6: the night is fitted to 6:00 to 7:27, and today's
        report has all of it (until 28 Nov 2026 part of it fell on the day
        before, and this test's neighbour failed from 6 to 9 every morning)."""
        from datetime import datetime, time

        from django.utils import timezone

        from accounts.models import User
        from core.utils import get_business_date
        from devtools.pub_night import start_the_night

        staff = {u.role: u for u in User.objects.filter(tenant=self.tenant)}
        morning = timezone.make_aware(datetime.combine(timezone.localdate(), time(7, 27)))
        start_the_night(self.tenant, self.outlet, staff, now=morning)
        today = get_business_date(morning, self.outlet)
        report = daily_sales(self.tenant, self.outlet, start_date=today, end_date=today)
        paid = sum(p.amount for p in Payment.objects.filter(order__outlet=self.outlet))
        self.assertEqual(D(str(report["total_sales"])).quantize(D("0.01")), paid)
        self.assertEqual(report["orders"], 8)
        self.assertTrue(all(p.paid_at <= morning for p in Payment.objects.filter(order__outlet=self.outlet)))

    def test_running_it_again_starts_the_night_over(self):
        call_command("seed_pub_demo", stdout=StringIO())
        self.assertEqual(Payment.objects.filter(order__outlet=self.outlet).count(), 8)
        self.assertEqual(Order.objects.filter(outlet=self.outlet).count(), 12)

    def test_the_cashier_can_open_the_floor_and_a_bill(self):
        client = Client()
        self.assertTrue(client.login(username="demo_pub_cashier", password="pubdemo123"))
        self.assertEqual(client.get("/tables/").status_code, 200)
        order = Order.objects.filter(outlet=self.outlet, status="open").first()
        self.assertContains(client.get(f"/bill/{order.id}/"), "Lager")
