"""
A bill never asks for less than the guest has already paid on it
(21 Nov 2026, found in the pub).

Reported: a cover paid part of a pub bill; then the guest took fewer items
and a promo went on, and the bill came to less than the cover had paid. The
bill screen said "FULLY PAID" with nothing to give back, the cover counted
the whole payment as spent, and the bill could not be closed.

orders/services/paid_guard.py now holds the rule for every change that ends
in Order.recalculate_totals: covers shrink back, money paid is given back
(on the spot with a manager, or first, as the outlet chooses).
Run: python manage.py test covers.tests.test_bill_drops_below_paid
"""
import json
from decimal import Decimal as D

from django.db import transaction
from django.test import Client, TestCase

from accounts.models import User
from covers.services import redeem_cover, sell_cover
from covers.tests.test_covers import bill, world
from orders.models import OrderItem, Payment
from orders.services import bill_discount
from orders.services.approvals import set_pin
from orders.services.paid_guard import BillBelowPaid, GiveBackNeeded, acting_as
from orders.services.payment_service import process_payment
from orders.services.void_service import reduce_item_quantity, void_order_item
from payments.models import Refund


class _TwoDishes(TestCase):
    """A bill of two dishes: 2 x 1000 + 5% GST = 2100, not yet printed."""

    def setUp(self):
        world(self)
        self.manager = User.objects.create_user(username="door_manager", password="pw", role="manager",
                                                tenant=self.tenant, outlet=self.outlet)
        set_pin(self.manager, "7392")
        self.order = bill(self, quantity=1, status="open")
        OrderItem.objects.create(order=self.order, menu_item=self.dish, quantity=1, price=D("1000"),
                                 gst_percentage=D("5"), total_price=D("1000"), status="sent")
        self.order.recalculate_totals()
        self.order.refresh_from_db()
        self.assertEqual(self.order.grand_total, D("2100.00"))

    def first_line(self):
        return self.order.items.exclude(status="voided").first()

    def fresh(self):
        self.order.refresh_from_db()
        return self.order

    def void_one(self, user=None, approver=None):
        with acting_as(user or self.cashier, approver):
            void_order_item(user or self.cashier, self.first_line().id, "guest changed mind", made=False,
                            approver=approver)


class ACoverShrinksBackTest(_TwoDishes):

    def setUp(self):
        super().setUp()
        self.cover = sell_cover(self.cashier, self.outlet, "2000", "cash")
        redeem_cover(self.cashier, self.order, self.cover.code)

    def test_taking_a_dish_off_brings_the_cover_payment_down_to_the_bill(self):
        self.void_one()
        order = self.fresh()
        self.assertEqual(order.grand_total, D("1050.00"))
        self.assertEqual(order.amount_paid(), D("1050.00"))
        self.cover.refresh_from_db()
        self.assertEqual(self.cover.balance(), D("950.00"), "the rest is the guest's to spend tonight")
        self.assertFalse(Refund.objects.exists(), "no money moves for a cover")

    def test_a_discount_after_the_cover_does_the_same(self):
        with acting_as(self.manager):
            with transaction.atomic():
                bill_discount.set_manual(self.order, "amount", "200")
                self.order.recalculate_totals()
        order = self.fresh()
        self.assertEqual(order.amount_paid(), order.grand_total)
        self.assertEqual(self.cover.balance(), D("2000.00") - order.grand_total)

    def test_fewer_of_a_dish_does_the_same(self):
        with acting_as(self.cashier):
            reduce_item_quantity(self.cashier, self.first_line().id, 1, "guest changed mind", made=False)
        order = self.fresh()
        self.assertEqual(order.amount_paid(), order.grand_total)

    def test_the_cover_change_is_on_the_record(self):
        self.void_one()
        event = self.order.events.filter(event_type="payment_refunded").get()
        self.assertEqual(event.metadata["to"], "cover")
        self.assertEqual(event.amount, D("950.00"))


class MoneyIsGivenBackOnTheSpotTest(_TwoDishes):
    """The outlet's rule is the default: a manager gives it back on the spot."""

    def setUp(self):
        super().setUp()
        process_payment(self.order, "cash", D("2000"), user=self.cashier)

    def test_a_cashier_alone_is_asked_for_a_manager(self):
        with self.assertRaises(GiveBackNeeded) as caught:
            self.void_one()
        self.assertIn("gives back ₹950.00", caught.exception.message)
        order = self.fresh()
        self.assertEqual(order.grand_total, D("2100.00"), "the dish is still on the bill")
        self.assertEqual(order.items.exclude(status="voided").count(), 2)
        self.assertFalse(Refund.objects.exists())

    def test_with_a_managers_pin_the_difference_is_given_back(self):
        self.void_one(approver=self.manager)
        order = self.fresh()
        self.assertEqual(order.grand_total, D("1050.00"))
        self.assertEqual(order.amount_paid(), D("1050.00"))
        refund = Refund.objects.get()
        self.assertEqual((refund.amount, refund.status, refund.payment.method, refund.refunded_by),
                         (D("950.00"), "approved", "cash", self.manager))
        # what shift close reads to take cash out of the drawer count
        self.assertTrue(Payment.objects.filter(method="refund", amount=D("-950.00"),
                                               reference=f"REFUND-{refund.id}").exists())

    def test_a_manager_doing_it_gives_it_back_themselves(self):
        self.void_one(user=self.manager)
        self.assertEqual(Refund.objects.get().refunded_by, self.manager)

    def test_a_refund_already_asked_for_is_not_given_back_twice(self):
        cash = self.order.payments.get(method="cash")
        Refund.objects.create(payment=cash, order=self.order, amount=D("300.00"), reason="cold food",
                              status="pending")
        self.void_one(user=self.manager)
        given = Refund.objects.get(status="approved")
        self.assertEqual(given.amount, D("650.00"), "950 over, 300 of it already on its way back")


class RefundFirstTest(_TwoDishes):

    def setUp(self):
        super().setUp()
        self.outlet.bill_below_paid = "refund_first"
        self.outlet.save(update_fields=["bill_below_paid"])
        self.order.outlet.refresh_from_db()
        process_payment(self.order, "card", D("2000"), user=self.cashier)

    def test_the_change_is_refused_until_the_difference_is_given_back(self):
        with self.assertRaises(BillBelowPaid) as caught:
            self.void_one(user=self.manager)
        self.assertIn("Give back ₹950.00 first", caught.exception.message)
        self.assertEqual(self.fresh().grand_total, D("2100.00"))

        from payments.refund_service import give_back
        give_back(self.fresh(), D("950.00"), self.manager, "guest changed mind")
        self.void_one(user=self.manager)
        order = self.fresh()
        self.assertEqual((order.grand_total, order.amount_paid()), (D("1050.00"), D("1050.00")))


class OnTheScreenTest(_TwoDishes):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.cashier)
        process_payment(self.order, "cash", D("2000"), user=self.cashier)

    def void(self, **headers):
        return self.client.post(f"/cancel-item/{self.first_line().id}/", data=json.dumps({"reason": "changed mind"}),
                                content_type="application/json", **headers)

    def test_the_cashier_gets_the_pin_prompt_then_the_give_back(self):
        first = self.void()
        self.assertEqual(first.status_code, 403, first.content)
        self.assertTrue(first.json()["needs_approval"])
        self.assertIn("gives back ₹950.00", first.json()["error"])
        again = self.void(HTTP_X_APPROVER_ID=str(self.manager.id), HTTP_X_APPROVER_PIN="7392")
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(self.fresh().amount_paid(), D("1050.00"))

    def test_the_amount_paid_counts_what_was_given_back(self):
        self.void(HTTP_X_APPROVER_ID=str(self.manager.id), HTTP_X_APPROVER_PIN="7392")
        order = self.fresh()
        self.assertEqual(order.payments.exclude(method="refund").count(), 1)
        self.assertEqual(order.amount_paid(), order.grand_total, "a refund row is taken off what was paid")


class AlreadyOverpaidBillTest(_TwoDishes):
    """A bill left with more paid than its total before this fix: the screen
    says so, a manager gives the difference back, then it closes."""

    def setUp(self):
        super().setUp()
        from orders.models import Order
        from shifts.models import CashSession
        from setup.models import PaymentConfig
        PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet, cash_enabled=True)
        CashSession.objects.create(tenant=self.tenant, outlet=self.outlet, opened_by=self.cashier,
                                   opening_balance=D("0"), status="open")
        process_payment(self.order, "cash", D("2000"), user=self.cashier)
        # As such a bill was left: the total came down with no guard.
        Order.objects.filter(pk=self.order.pk).update(grand_total=D("1050.00"))
        self.client = Client()

    def page(self, user):
        self.client.force_login(user)
        return self.client.get(f"/bill/{self.order.id}/").content.decode()

    def post(self, url, body, **headers):
        return self.client.post(url, data=json.dumps(body), content_type="application/json", **headers)

    def test_the_bill_says_overpaid_not_fully_paid(self):
        page = self.page(self.cashier)
        self.assertIn("Overpaid ₹950.00", page)
        self.assertIn('data-action="pay.give_back"', page)
        self.assertNotIn("FULLY PAID", page)

    def test_it_cannot_be_closed_until_the_money_is_given_back(self):
        self.client.force_login(self.cashier)
        resp = self.post(f"/pay/{self.order.id}/", {"method": "cash", "amount": "0"})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn("Give it back first", resp.json()["error"])
        self.assertEqual(self.fresh().status, "open")

    def test_a_cashier_needs_a_managers_pin_to_give_back(self):
        self.client.force_login(self.cashier)
        resp = self.post(f"/give-back/{self.order.id}/", {"amount": "950", "reason": "fewer dishes"})
        self.assertEqual(resp.status_code, 403, resp.content)
        self.assertTrue(resp.json()["needs_approval"])
        resp = self.post(f"/give-back/{self.order.id}/", {"amount": "950", "reason": "fewer dishes"},
                         HTTP_X_APPROVER_ID=str(self.manager.id), HTTP_X_APPROVER_PIN="7392")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Refund.objects.get().refunded_by, self.manager)

    def test_given_back_it_shows_paid_in_full_and_closes(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post(f"/give-back/{self.order.id}/", {"amount": "950"}).status_code, 200)
        page = self.page(self.manager)
        self.assertIn("Close Bill (Paid In Full)", page)
        self.assertIn("Given back", page)
        resp = self.post(f"/pay/{self.order.id}/", {"method": "cash", "amount": "0"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["message"], "Bill paid in full and closed")
        self.assertEqual(self.fresh().status, "closed")

    def test_no_more_than_was_paid_in_money_goes_back(self):
        self.client.force_login(self.manager)
        resp = self.post(f"/give-back/{self.order.id}/", {"amount": "2000.01"})
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertFalse(Refund.objects.exists())

    def test_another_restaurants_bill_is_not_found(self):
        world(self, name="Other")
        stranger = User.objects.create_user(username="other_manager", password="pw", role="manager",
                                            tenant=self.tenant, outlet=self.outlet)
        self.client.force_login(stranger)
        resp = self.post(f"/give-back/{self.order.id}/", {"amount": "10"})
        self.assertEqual(resp.status_code, 404, resp.content)


class RepairCommandTest(_TwoDishes):
    """python manage.py bills_below_paid: bills left overpaid before the fix."""

    def setUp(self):
        super().setUp()
        from orders.models import Order
        self.cover = sell_cover(self.cashier, self.outlet, "2000", "cash")
        redeem_cover(self.cashier, self.order, self.cover.code)
        Order.objects.filter(pk=self.order.pk).update(grand_total=D("1050.00"))   # as left before the fix

    def run_it(self, *args):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command("bills_below_paid", *args, stdout=out)
        return out.getvalue()

    def test_it_lists_and_changes_nothing_by_default(self):
        out = self.run_it()
        self.assertIn("950.00 over on a cover", out)
        self.assertIn("Nothing was changed", out)
        self.assertEqual(self.fresh().amount_paid(), D("2000.00"))

    def test_apply_brings_the_cover_down_to_the_bill(self):
        out = self.run_it("--apply")
        self.assertIn("cover brought down by 950.00", out)
        self.assertEqual(self.fresh().amount_paid(), D("1050.00"))
        self.assertEqual(self.cover.balance(), D("950.00"))
        self.assertIn("No open bill", self.run_it())
