"""
A paid bill never changes, even when a screen is holding an older copy of it.

Order.recalculate_totals() refuses to re-total an issued bill (paid or
closed, already totalled). These tests pin the rest of that promise:

  * the refusal also comes from the database, not only from the copy in
    hand: a screen can hold a copy read before another screen took the
    payment
  * the refusal reaches the user as a plain message, never an error page
  * adding items and toggling the parcel charge lock the order before they
    change it, so a payment landing at the same moment can't be undone or
    re-totalled (the race tests hold the order's row lock, start the
    request, take the payment, and only then let the request go on: the
    same interleaving every time, whichever thread the OS runs first)

Run: python manage.py test orders.tests.test_issued_bill_guard
"""
import json
import threading
import time
from decimal import Decimal as D
from unittest import skipUnless

from django.db import connection, transaction
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.exceptions import OrderError
from orders.models import IssuedBillError, Order, OrderItem, Table
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant


def _world(test):
    """An outlet with a ₹10 flat parcel charge, a table and a 5% ₹100 curry."""
    test.tenant = Tenant.objects.create(name="Issued Bill Guard")
    test.outlet = Outlet.objects.create(
        tenant=test.tenant, name="Main", gst_no=SAMPLE_GSTIN,
        parcel_charge_amount=D("10"), parcel_charge_per_item=False,
    )
    test.owner = User.objects.create_user(
        username="guard_owner", password="pw", role="owner",
        tenant=test.tenant, outlet=test.outlet,
    )
    test.cashier = User.objects.create_user(
        username="guard_cashier", password="pw", role="cashier",
        tenant=test.tenant, outlet=test.outlet,
    )
    test.table = Table.objects.create(tenant=test.tenant, outlet=test.outlet, name="T1")
    category = MenuCategory.objects.create(tenant=test.tenant, outlet=test.outlet, name="Mains")
    test.curry = MenuItem.objects.create(
        tenant=test.tenant, outlet=test.outlet, category=category,
        name="Curry", price=D("100"), gst_percentage=D("5"),
    )


def _open_order(test, quantity=2):
    """An open order on the table with `quantity` curries, totalled."""
    order = Order.objects.create(
        tenant=test.tenant, outlet=test.outlet, table=test.table,
        created_by=test.cashier, source="dine_in", status="open",
    )
    OrderItem.objects.create(
        order=order, menu_item=test.curry, quantity=quantity, price=test.curry.price,
        gst_percentage=test.curry.gst_percentage, total_price=test.curry.price * quantity,
        status="sent",
    )
    order.recalculate_totals()
    return order


def _client(user):
    client = Client()
    client.force_login(user)
    return client


class IssuedBillScreensTest(TestCase):

    def setUp(self):
        _world(self)

    def test_a_stale_copy_of_a_paid_bill_is_not_re_totalled(self):
        order = _open_order(self)
        stale = Order.objects.get(pk=order.pk)          # a screen's copy, read while open
        Order.objects.filter(pk=order.pk).update(status="paid")

        with self.assertRaises(IssuedBillError):
            stale.recalculate_totals()
        order.refresh_from_db()
        self.assertEqual((order.status, order.grand_total), ("paid", D("210")))

    def test_the_refusal_is_an_order_error_with_a_plain_message(self):
        error = IssuedBillError(7, "closed")
        self.assertIsInstance(error, OrderError)
        self.assertEqual(error.order_id, 7)
        self.assertEqual(str(error), "This bill is already closed, so it can't be changed. Correct it with a refund.")

    def test_parcel_toggle_works_on_an_open_bill_both_ways(self):
        order = _open_order(self)
        client = _client(self.cashier)

        on = client.post(reverse("toggle-parcel", args=[order.id]))
        self.assertEqual(on.status_code, 200)
        self.assertTrue(on.json()["parcel_on"])
        order.refresh_from_db()
        self.assertEqual(order.parcel_surcharge, D("10.00"))
        self.assertEqual(order.grand_total, D("221"))    # 200 + 10 GST + 10 parcel + 0.50 GST, rounded

        off = client.post(reverse("toggle-parcel", args=[order.id]))
        self.assertFalse(off.json()["parcel_on"])
        order.refresh_from_db()
        self.assertEqual((order.parcel_surcharge, order.grand_total), (D("0.00"), D("210")))

    def test_parcel_toggle_on_a_paid_bill_is_refused_and_changes_nothing(self):
        order = _open_order(self)
        Order.objects.filter(pk=order.pk).update(status="paid")

        resp = _client(self.cashier).post(reverse("toggle-parcel", args=[order.id]))

        self.assertEqual(resp.status_code, 404)
        self.assertIn("error", resp.json())
        order.refresh_from_db()
        self.assertEqual((order.parcel_surcharge, order.grand_total), (D("0.00"), D("210")))

    def test_an_item_discount_on_a_paid_bill_is_refused_with_a_message(self):
        order = _open_order(self)
        Order.objects.filter(pk=order.pk).update(status="paid")
        item = order.items.get()

        resp = _client(self.cashier).post(
            reverse("item-discount", args=[item.id]),
            data=json.dumps({"percent": 50}), content_type="application/json",
        )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Order is locked and cannot be modified")
        item.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((item.item_discount_pct, order.grand_total), (D("0.00"), D("210")))

    def test_making_a_dish_free_on_a_paid_bill_is_refused_with_a_message(self):
        order = _open_order(self)
        Order.objects.filter(pk=order.pk).update(status="paid")
        item = order.items.get()

        resp = _client(self.owner).post(reverse("make-complimentary", args=[item.id]))

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Order is locked and cannot be modified")
        item.refresh_from_db()
        self.assertFalse(item.is_complimentary)

    def test_adding_items_still_saves_the_customer_and_the_discount(self):
        # create_order now saves only the fields it changes; they must all land
        order = _open_order(self, quantity=1)

        resp = _client(self.cashier).post(
            reverse("create-order"), content_type="application/json",
            data=json.dumps({
                "cart": [{"id": self.curry.id, "quantity": 1}],
                "order_id": order.id, "table_id": self.table.id, "source": "dine_in",
                "customer_name": "Asha", "customer_phone": "9876543210",
                "discount_type": "percentage", "discount_value": "10", "discount_reason": "regular guest",
            }),
        )

        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["order_id"], order.id)
        order.refresh_from_db()
        self.assertEqual((order.customer_name, order.customer_phone), ("Asha", "9876543210"))
        self.assertEqual((order.discount_type, order.discount_value), ("percentage", D("10.00")))
        self.assertEqual(order.items.count(), 2)
        self.assertEqual(order.grand_total, D("189"))    # 200 less 10% = 180, + 5% GST


@skipUnless(connection.vendor == "postgresql", "row locks are tested on Postgres")
class IssuedBillRaceTest(TransactionTestCase):
    """A payment lands while a request that changes the bill is under way."""

    def setUp(self):
        _world(self)

    def _pay_while_request_waits(self, order, request):
        """Hold the order's row lock (as a payment does), start `request` in
        its own thread, wait until it is blocked on that lock, mark the
        order paid, commit, and return the request's response."""
        outcome = {}

        def run():
            try:
                outcome["response"] = request()
            except Exception as e:  # noqa: BLE001 -- reported by the assertion below
                outcome["error"] = e
            finally:
                connection.close()

        with transaction.atomic():
            Order.objects.select_for_update().get(pk=order.pk)
            worker = threading.Thread(target=run)
            worker.start()
            self._wait_until_a_lock_is_awaited()
            Order.objects.filter(pk=order.pk).update(status="paid")
        worker.join(timeout=30)
        self.assertFalse(worker.is_alive(), "the request never finished")
        self.assertNotIn("error", outcome, outcome.get("error"))
        return outcome["response"]

    def _wait_until_a_lock_is_awaited(self):
        # Only this test database's sessions: a parallel test run has its own.
        deadline = time.monotonic() + 15
        with connection.cursor() as cursor:
            while time.monotonic() < deadline:
                cursor.execute(
                    "SELECT count(*) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid "
                    "WHERE NOT l.granted AND a.datname = current_database()"
                )
                if cursor.fetchone()[0]:
                    return
                time.sleep(0.02)
        self.fail("the request never waited for the order's lock")

    def test_adding_items_during_a_payment_is_refused_not_reopened(self):
        order = _open_order(self)
        client = _client(self.cashier)

        resp = self._pay_while_request_waits(order, lambda: client.post(
            reverse("create-order"), content_type="application/json",
            data=json.dumps({
                "cart": [{"id": self.curry.id, "quantity": 1}],
                "order_id": order.id, "table_id": self.table.id, "source": "dine_in",
            }),
        ))

        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertIn("just paid", resp.json()["error"])
        order.refresh_from_db()
        self.assertEqual((order.status, order.grand_total), ("paid", D("210")))
        self.assertEqual(order.items.count(), 1)

    def test_parcel_toggle_during_a_payment_changes_nothing(self):
        order = _open_order(self)
        client = _client(self.cashier)

        resp = self._pay_while_request_waits(
            order, lambda: client.post(reverse("toggle-parcel", args=[order.id])))

        self.assertEqual(resp.status_code, 404, resp.content)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual((order.parcel_surcharge, order.grand_total), (D("0.00"), D("210")))

    def test_a_stale_copy_is_refused_outside_a_transaction_too(self):
        order = _open_order(self)
        stale = Order.objects.get(pk=order.pk)
        Order.objects.filter(pk=order.pk).update(status="closed")

        with self.assertRaises(IssuedBillError):
            stale.recalculate_totals()          # autocommit: the unlocked read still sees it
        order.refresh_from_db()
        self.assertEqual(order.grand_total, D("210"))
