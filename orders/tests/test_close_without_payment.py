"""
Closing a bill without payment (the "payment bypass").

Found 3 Oct 2026 by running it:
  * The manager's back arrow on an unpaid bill ("Dashboard (Bypass)") closed
    the bill with no payment, freed the table and swallowed any error: going
    back to the tables lost the bill's money without a word.
  * No reason was asked for, and nothing showed it on any report.
  * A manager's limit of 3 a day reset at midnight, so a manager could close
    3 before midnight and 3 more after, in one business night.
  * A cancelled bill could be "closed".

Now: the back arrow only goes back; "Close Without Payment" is its own
button with a confirm and a reason; the limit counts by the business day;
each one is on the discount/void audit report with the amount unpaid.

Run: python manage.py test orders.tests.test_close_without_payment
"""
import json
from datetime import datetime
from decimal import Decimal as D
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem, Payment, Table
from reports.services.audit_reports import discount_void_audit
from tenants.models import Outlet, Tenant

IST = ZoneInfo("Asia/Kolkata")


def at(year, month, day, hour, minute=0):
    return mock.patch("django.utils.timezone.now",
                      return_value=datetime(year, month, day, hour, minute, tzinfo=IST))


class CloseWithoutPaymentTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Walkout Pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = self.user("cw_owner", "owner")
        self.manager = self.user("cw_manager", "manager")
        self.cashier = self.user("cw_cashier", "cashier")
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1", state="billing")
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                            name="Pitcher", price=D("1000"), gst_percentage=D("0"))

    def user(self, username, role):
        return User.objects.create_user(username=username, password="pw", role=role,
                                        tenant=self.tenant, outlet=self.outlet)

    def bill(self, table=None):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                     status="open", source="dine_in", table=table)
        OrderItem.objects.create(order=order, menu_item=self.dish, quantity=1, price=D("1000"),
                                 gst_percentage=D("0"), total_price=D("1000"), status="served")
        order.recalculate_totals()
        return order

    def close(self, user, order, reason="guest walked out"):
        client = Client()
        client.force_login(user)
        body = {} if reason is None else {"reason": reason}
        return client.post(reverse("log-bypass", args=[order.id]), data=json.dumps(body),
                           content_type="application/json")

    # ---------------------------------------------------------------- reason
    def test_a_reason_is_needed(self):
        order = self.bill()
        for reason in (None, "", "  ", "ok"):
            resp = self.close(self.manager, order, reason)
            self.assertEqual(resp.status_code, 400, reason)
            self.assertEqual(resp.json()["error"],
                             "Give a reason for closing the bill without payment (for example: guest walked out).")
        order.refresh_from_db()
        self.assertEqual(order.status, "open")

    def test_it_closes_and_records_who_why_and_how_much(self):
        order = self.bill(self.table)
        Payment.objects.create(order=order, method="cash",
                               amount=D("400"))
        resp = self.close(self.manager, order, "  guest   walked out  ")
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.table.refresh_from_db()
        self.assertEqual(order.status, "closed")
        self.assertEqual(self.table.state, "free")
        event = OrderEvent.objects.get(order=order, metadata__action="payment_gate_bypassed")
        self.assertEqual(event.metadata["reason"], "guest walked out")
        self.assertEqual(event.metadata["unpaid"], "600.00")
        self.assertEqual(event.metadata["paid"], "400.00")
        self.assertEqual(event.created_by, self.manager)

    def test_a_closed_or_cancelled_bill_is_left_alone(self):
        for status, message in (("closed", "This bill is already closed."),
                                ("paid", "This bill is already closed."),
                                ("cancelled", "This bill was cancelled.")):
            order = self.bill()
            Order.objects.filter(pk=order.pk).update(status=status)
            resp = self.close(self.owner, order)
            self.assertEqual(resp.status_code, 400)
            self.assertEqual(resp.json()["error"], message)
            order.refresh_from_db()
            self.assertEqual(order.status, status)

    def test_a_cashier_cannot(self):
        self.assertEqual(self.close(self.cashier, self.bill()).status_code, 403)

    # ----------------------------------------------------------- the limit
    def test_the_limit_counts_the_whole_business_night(self):
        # 3 before midnight, then 1 AM is still the same business day (it
        # starts at 6 AM): the old midnight reset allowed 3 more here.
        with at(2026, 10, 3, 23, 0):
            for _ in range(3):
                self.assertEqual(self.close(self.manager, self.bill()).status_code, 200)
        with at(2026, 10, 4, 1, 0):
            late = self.bill()
            resp = self.close(self.manager, late)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"],
                         "You have closed 3 bills without payment today, the most a manager can. "
                         "Ask the owner to close this one.")
        late.refresh_from_db()
        self.assertEqual(late.status, "open")
        # The next business day starts at 6 AM.
        with at(2026, 10, 4, 6, 30):
            self.assertEqual(self.close(self.manager, late).status_code, 200)

    def test_the_owner_has_no_limit(self):
        with at(2026, 10, 3, 23, 0):
            for _ in range(5):
                self.assertEqual(self.close(self.owner, self.bill()).status_code, 200)

    # ------------------------------------------------------------- report
    def test_the_audit_report_lists_each_one(self):
        with at(2026, 10, 3, 23, 30):
            order = self.bill()
            self.close(self.manager, order, "guest walked out")
        report = discount_void_audit(self.tenant, self.outlet, datetime(2026, 10, 3).date(),
                                     datetime(2026, 10, 3).date())
        self.assertEqual(len(report["unpaid_closes"]), 1)
        row = report["unpaid_closes"][0]
        order.refresh_from_db()
        self.assertEqual(row["bill"], order.display_number)
        self.assertEqual(row["unpaid"], D("1000.00"))
        self.assertEqual(row["by"], "cw_manager")
        self.assertEqual(row["reason"], "guest walked out")

    def test_one_closed_before_reasons_still_shows_its_amount(self):
        # An event from before 3 Oct 2026 has no amount or reason recorded.
        with at(2026, 10, 2, 22, 0):
            order = self.bill()
            Order.objects.filter(pk=order.pk).update(status="closed")
            OrderEvent.objects.create(tenant=self.tenant, outlet=self.outlet, order=order,
                                      event_type="status_changed", created_by=self.manager,
                                      metadata={"action": "payment_gate_bypassed", "role": "manager",
                                                "bypassed_by": "cw_manager"})
        day = datetime(2026, 10, 2).date()
        row = discount_void_audit(self.tenant, self.outlet, day, day)["unpaid_closes"][0]
        self.assertEqual(row["unpaid"], D("1000.00"))
        self.assertEqual(row["reason"], "")

    # ------------------------------------------------------------ bill page
    def test_going_back_never_closes_the_bill(self):
        order = self.bill(self.table)
        client = Client()
        client.force_login(self.manager)
        page = client.get(reverse("bill-view", args=[order.id])).content.decode()
        self.assertNotIn("logBypass", page)
        self.assertIn('id="close-unpaid"', page)
        self.assertIn('href="/tables/" class="nav-back', page)

    def test_a_cashier_sees_no_close_button(self):
        order = self.bill()
        client = Client()
        client.force_login(self.cashier)
        page = client.get(reverse("bill-view", args=[order.id])).content.decode()
        self.assertNotIn('id="close-unpaid"', page)
