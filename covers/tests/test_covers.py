"""
Cover charges (covers/), 20 Oct 2026.

A pub sells entry at the door that the guest spends on food and drinks.
Rasova had no way to do it: staff gave a discount for it, which took tax
off the bill and left no record of the door's money. Now a cover is a
voucher: it pays part of a bill like any payment, the bill and its tax
stay as they are, it pays several bills until it runs out, it lapses after
its night, and its cash counts in the drawer when the shift closes.

Run: python manage.py test covers
"""
import json
import threading
from datetime import timedelta
from decimal import Decimal as D

from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import User
from covers.models import Cover
from covers.services import CODE_LETTERS, CoverError, night_summary, redeem_cover, sell_cover
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from shifts.models import CashSession
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant


def world(test, name="Door"):
    test.tenant = Tenant.objects.create(name=f"{name} Pub", tenant_type="pub")
    test.outlet = Outlet.objects.create(tenant=test.tenant, name="Main", gst_no=SAMPLE_GSTIN)
    test.cashier = User.objects.create_user(username=f"{name.lower()}_cashier", password="pw", role="cashier",
                                            tenant=test.tenant, outlet=test.outlet)
    cat = MenuCategory.objects.create(tenant=test.tenant, outlet=test.outlet, name="Food")
    test.dish = MenuItem.objects.create(tenant=test.tenant, outlet=test.outlet, category=cat, name="Nachos",
                                        price=D("1000"), gst_percentage=D("5"))


def bill(test, quantity=1, status="billing"):
    order = Order.objects.create(tenant=test.tenant, outlet=test.outlet, created_by=test.cashier,
                                 source="dine_in", status="open")
    OrderItem.objects.create(order=order, menu_item=test.dish, quantity=quantity, price=D("1000"),
                             gst_percentage=D("5"), total_price=D("1000") * quantity, status="sent")
    order.recalculate_totals()
    if status != "open":
        order.status = status
        order.save()
    order.refresh_from_db()
    return order


class SellTest(TestCase):
    def setUp(self):
        world(self)

    def test_a_cover_gets_a_short_code_for_tonight(self):
        cover = sell_cover(self.cashier, self.outlet, "2000", "cash", guests=2, guest_name="Asha")
        self.assertEqual(len(cover.code), 5)
        self.assertTrue(set(cover.code) <= set(CODE_LETTERS))
        self.assertEqual((cover.amount, cover.guests, cover.paid_by), (D("2000.00"), 2, "cash"))

    def test_what_a_cover_must_have(self):
        for amount, paid_by, guests in (("0", "cash", 1), ("abc", "cash", 1), ("500", "cheque", 1),
                                        ("500", "cash", 0), ("500", "cash", 51), ("200000", "upi", 1)):
            with self.assertRaises(CoverError, msg=(amount, paid_by, guests)):
                sell_cover(self.cashier, self.outlet, amount, paid_by, guests=guests)
        self.assertFalse(Cover.objects.exists())


class RedeemTest(TestCase):
    def setUp(self):
        world(self)

    def test_a_cover_pays_part_of_the_bill_and_the_bill_and_its_tax_stay(self):
        order = bill(self)
        before = (order.grand_total, order.gst_total)
        cover = sell_cover(self.cashier, self.outlet, "1000", "cash")
        redemption = redeem_cover(self.cashier, order, cover.code.lower())       # typed in lower case
        order.refresh_from_db()
        self.assertEqual((order.grand_total, order.gst_total), before)
        self.assertEqual((redemption.amount, redemption.payment.method), (D("1000.00"), "cover"))
        self.assertEqual(order.status, "billing")                               # Rs 50 still due
        self.assertEqual(cover.balance(), D("0.00"))

    def test_a_big_cover_settles_the_bill_and_the_rest_pays_the_next_one(self):
        cover = sell_cover(self.cashier, self.outlet, "2000", "upi")
        first = bill(self)
        redeem_cover(self.cashier, first, cover.code)
        first.refresh_from_db()
        self.assertEqual(first.status, "closed")
        self.assertEqual(cover.balance(), D("950.00"))
        second = bill(self)
        self.assertEqual(redeem_cover(self.cashier, second, cover.code).amount, D("950.00"))
        with self.assertRaisesMessage(CoverError, "used up"):
            redeem_cover(self.cashier, bill(self), cover.code)

    def test_yesterdays_cover_has_lapsed(self):
        cover = sell_cover(self.cashier, self.outlet, "1000", "cash")
        Cover.objects.filter(id=cover.id).update(business_date=cover.business_date - timedelta(days=1))
        with self.assertRaisesMessage(CoverError, "good only on its night"):
            redeem_cover(self.cashier, bill(self), cover.code)

    def test_another_outlets_cover_is_not_taken(self):
        other = Outlet.objects.create(tenant=self.tenant, name="Other")
        cover = sell_cover(self.cashier, other, "1000", "cash")
        with self.assertRaisesMessage(CoverError, "No cover"):
            redeem_cover(self.cashier, bill(self), cover.code)

    def test_a_cancelled_bill_gives_its_part_back(self):
        order = bill(self, status="open")
        cover = sell_cover(self.cashier, self.outlet, "1000", "cash")
        redeem_cover(self.cashier, order, cover.code)
        Order.objects.filter(id=order.id).update(status="cancelled")
        self.assertEqual(cover.balance(), D("1000.00"))

    def test_the_bill_screen_pays_from_a_code(self):
        order = bill(self)
        cover = sell_cover(self.cashier, self.outlet, "500", "cash")
        client = Client()
        client.force_login(self.cashier)
        page = client.get(f"/bill/{order.id}/")
        self.assertContains(page, 'id="pay-cover"')
        resp = client.post(reverse("cover-redeem", args=[order.id]), data=json.dumps({"code": cover.code}),
                           content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["amount"], 500.0)
        resp = client.post(reverse("cover-redeem", args=[order.id]), data=json.dumps({"code": "ZZZZZ"}),
                           content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_the_night_adds_up(self):
        a = sell_cover(self.cashier, self.outlet, "1000", "cash", guests=2)
        sell_cover(self.cashier, self.outlet, "500", "upi")
        redeem_cover(self.cashier, bill(self), a.code)
        night = night_summary(self.tenant, self.outlet, a.business_date)
        self.assertEqual((night["sold"], night["used"], night["left"], night["guests"]),
                         (D("1500.00"), D("1000.00"), D("500.00"), 3))
        self.assertEqual(night["sold_by_method"], {"cash": D("1000.00"), "upi": D("500.00"), "card": D("0")})


class DoorPageTest(TestCase):
    def setUp(self):
        world(self)

    def test_the_door_sells_and_shows_the_slip(self):
        client = Client()
        client.force_login(self.cashier)
        resp = client.post(reverse("covers"), {"amount": "1500", "paid_by": "card", "guests": "3"}, follow=True)
        cover = Cover.objects.get()
        self.assertContains(resp, f'<div class="slip-code" id="slipCode">{cover.code}</div>', html=True)
        self.assertContains(resp, "1500.00")

    def test_a_cafe_has_no_covers(self):
        cafe = Tenant.objects.create(name="Plain Cafe", tenant_type="cafe")
        outlet = Outlet.objects.create(tenant=cafe, name="Main")
        owner = User.objects.create_user(username="plain_owner", password="pw", role="owner", tenant=cafe,
                                         outlet=outlet)
        client = Client()
        client.force_login(owner)
        self.assertNotEqual(client.get(reverse("covers")).status_code, 200)


class ShiftCountsTheDoorTest(TestCase):
    def setUp(self):
        world(self)

    def test_cash_covers_are_expected_in_the_drawer(self):
        CashSession.objects.create(tenant=self.tenant, outlet=self.outlet, opened_by=self.cashier,
                                   opening_balance=D("1000"), status="open")
        sell_cover(self.cashier, self.outlet, "2000", "cash")
        sell_cover(self.cashier, self.outlet, "700", "upi")
        client = Client()
        client.force_login(self.cashier)
        resp = client.post(reverse("close-cash-session"), data=json.dumps({"actual_cash": "3000"}),
                           content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        session = CashSession.objects.get()
        self.assertEqual((session.expected_cash, session.discrepancy, session.total_digital_payments),
                         (D("3000.00"), D("0.00"), D("700.00")))


class TwoBillsOneCoverTest(TransactionTestCase):
    """Two cashiers use the same cover at the same moment: it is spent once."""

    def test_no_overspend(self):
        world(self, name="Race")
        cover = sell_cover(self.cashier, self.outlet, "1000", "cash")
        orders = [bill(self), bill(self)]
        results = []
        barrier = threading.Barrier(2)

        def use(order):
            try:
                barrier.wait()
                redeem_cover(self.cashier, order, cover.code)
                results.append("paid")
            except CoverError as e:
                results.append(str(e))
            finally:
                connection.close()

        threads = [threading.Thread(target=use, args=(o,)) for o in orders]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), sorted(["paid", f"Cover {cover.code} is used up."]))
        self.assertEqual(cover.balance(), D("0.00"))
