"""
A printed bill can't be lowered without a manager (20 Oct 2026).

The leak check of 19 Oct 2026 proved, by running the code, that once the bill
was printed (status "billing", bill number issued) a cashier or captain
could take money off it on their own:
  L1   void a dish the guest ate: 1,000 -> 0, no manager;
  L1b  cut a quantity on a line not yet sent: 3,000 -> 1,000, logged as an
       "item_updated" only, so not on the Discount & Void audit;
  L1c  cancel the whole printed bill;
  L1d  a captain voids a ready dish as wastage, no manager;
  L2   any staff tags an order "zomato" with no Zomato order ID, so its GST
       disappears.
Also found while fixing: a bill discount and taking the parcel charge off
were allowed on a printed bill for anyone, while a dish discount or a free
dish was refused for everyone, managers included. And a bar's drink counted
as "probably not made" for 10 minutes after it was sent, so a voided drink
went back into stock.

Now (orders/services/approvals.py): anything that lowers a printed bill, and
any cancel of a dish already made, needs a manager doing it or a manager's
PIN typed on the screen; each approved change to a printed bill is on the
audit with the bill before and after and whose PIN approved it.

Every test in the leak classes fails on the code before this change.

Run: python manage.py test orders.tests.test_printed_bill_approval
"""
import json
from datetime import timedelta
from decimal import Decimal as D

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date
from kitchen.models import KOTBatch
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem
from orders.services import approvals
from reports.services.audit_reports import discount_void_audit
from setup.models import KitchenStation
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant

PIN = "2580"
FAST_HASHER = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Approval Pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no=SAMPLE_GSTIN)
        self.other_outlet = Outlet.objects.create(tenant=self.tenant, name="Second", gst_no=SAMPLE_GSTIN)
        self.users = {
            role: User.objects.create_user(username=f"ap_{role}", password="pw", role=role, first_name=role.title(),
                                           tenant=self.tenant, outlet=self.outlet)
            for role in ("owner", "manager", "cashier", "captain")
        }
        approvals.set_pin(self.users["manager"], PIN)
        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=cat,
                                            name="Biryani", price=D("1000"), gst_percentage=D("5"))
        self.kitchen = KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name="Kitchen",
                                                     is_default=True)
        self.bar = KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar",
                                                 made_on_send=True)

    # ── an order in a given state ──────────────────────────────────────
    def order(self, status="billing", quantity=1, item_status="pending", station=None, sent_ago=None,
              source="takeaway", parcel=D("0")):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.users["cashier"],
                                     status="open", source=source, parcel_surcharge=parcel)
        kot = None
        if item_status != "pending":
            kot = KOTBatch.objects.create(tenant=self.tenant, outlet=self.outlet, order=order, kot_number=1,
                                          station=station or self.kitchen)
            if sent_ago is not None:
                KOTBatch.objects.filter(id=kot.id).update(created_at=timezone.now() - sent_ago)
        self.item = OrderItem.objects.create(order=order, menu_item=self.dish, quantity=quantity, price=D("1000"),
                                             gst_percentage=D("5"), total_price=D("1000") * quantity,
                                             status=item_status, kot=kot)
        order.recalculate_totals()
        if status != "open":
            order.status = status
            order.save()
        order.refresh_from_db()
        return order

    def post(self, role, url, body=None, pin=None, approver=None):
        client = Client()
        client.force_login(self.users[role])
        headers = {}
        if pin is not None:
            headers = {"HTTP_X_APPROVER_ID": str((approver or self.users["manager"]).id), "HTTP_X_APPROVER_PIN": pin}
        return client.post(url, data=json.dumps(body or {}), content_type="application/json", **headers)

    def void(self, role, order, **kw):
        return self.post(role, reverse("cancel-item", args=[order.items.exclude(status="voided").first().id]),
                         {"reason": "Guest changed mind"}, **kw)

    def assertNeedsApproval(self, resp):
        self.assertEqual(resp.status_code, 403, resp.content)
        data = resp.json()
        self.assertTrue(data["needs_approval"])
        self.assertEqual([a["name"] for a in data["approvers"]], ["Manager"])

    def bill(self, order):
        order.refresh_from_db()
        return order.grand_total


class PrintedBillLeakTest(Base):
    """L1, L1b, L1c and the other ways down: refused for a cashier or captain
    with no PIN, and the bill doesn't move."""

    def test_voiding_a_dish_on_a_printed_bill_needs_a_manager(self):          # L1
        order = self.order()
        resp = self.void("cashier", order)
        self.assertNeedsApproval(resp)
        self.assertEqual(self.bill(order), D("1050.00"))
        self.assertEqual(order.items.get().status, "pending")

    def test_cutting_a_quantity_on_a_printed_bill_needs_a_manager(self):      # L1b
        order = self.order(quantity=3)
        resp = self.post("cashier", reverse("reduce-item", args=[self.item.id]), {"reduce_by": 2, "reason": "x"})
        self.assertNeedsApproval(resp)
        self.assertEqual(self.bill(order), D("3150.00"))

    def test_cancelling_a_printed_bill_needs_a_manager(self):                 # L1c
        order = self.order()
        resp = self.post("cashier", reverse("cancel-order", args=[order.id]), {"reason": "Guest left"})
        self.assertNeedsApproval(resp)
        order.refresh_from_db()
        self.assertEqual(order.status, "billing")

    def test_a_bill_discount_on_a_printed_bill_needs_a_manager(self):
        order = self.order()
        resp = self.post("cashier", reverse("apply-discount", args=[order.id]),
                         {"type": "percentage", "value": 50, "reason": "Regular guest"})
        self.assertNeedsApproval(resp)
        self.assertEqual(self.bill(order), D("1050.00"))

    def test_a_dish_discount_on_a_printed_bill_asks_for_a_manager(self):
        # It used to be refused outright, even for a manager (400).
        order = self.order()
        resp = self.post("cashier", reverse("item-discount", args=[self.item.id]), {"percent": 50, "reason": "Cold"})
        self.assertNeedsApproval(resp)
        self.assertEqual(self.bill(order), D("1050.00"))

    def test_a_free_dish_on_a_printed_bill_asks_for_a_manager(self):
        order = self.order()
        resp = self.post("captain", reverse("make-complimentary", args=[self.item.id]), {"reason": "Birthday"})
        self.assertNeedsApproval(resp)
        self.assertEqual(self.bill(order), D("1050.00"))

    def test_taking_the_parcel_charge_off_a_printed_bill_needs_a_manager(self):
        order = self.order(parcel=D("40"))
        before = self.bill(order)
        resp = self.post("cashier", reverse("toggle-parcel", args=[order.id]))
        self.assertNeedsApproval(resp)
        self.assertEqual(self.bill(order), before)

    def test_re_tagging_a_printed_bill_as_an_app_order_needs_a_manager(self):
        # Adding a dish in the same save must not hide the GST that goes.
        order = self.order()
        resp = self.post("cashier", reverse("create-order"), {
            "order_id": order.id, "source": "zomato", "aggregator_id": "ZOM-123",
            "cart": [{"id": self.dish.id, "quantity": 1}],
        })
        self.assertNeedsApproval(resp)
        order.refresh_from_db()
        self.assertEqual((order.source, order.items.count(), order.grand_total), ("takeaway", 1, D("1050.00")))


class AppOrderNeedsItsIdTest(Base):                                          # L2
    def test_an_app_order_without_the_app_order_id_is_refused(self):
        resp = self.post("cashier", reverse("create-order"), {
            "source": "zomato", "cart": [{"id": self.dish.id, "quantity": 1}],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("order ID", resp.json()["error"])
        self.assertFalse(Order.objects.filter(tenant=self.tenant, source="zomato").exists())

    def test_with_the_id_it_is_taken(self):
        resp = self.post("cashier", reverse("create-order"), {
            "source": "zomato", "aggregator_id": "ZOM-9", "cart": [{"id": self.dish.id, "quantity": 1}],
        })
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual((order.source, order.aggregator_order_id), ("zomato", "ZOM-9"))


class MadeDishTest(Base):                                                    # L1d
    def test_a_captain_cancelling_a_ready_dish_as_wastage_needs_a_manager(self):
        order = self.order(status="open", item_status="ready")
        resp = self.void("captain", order)
        self.assertNeedsApproval(resp)
        self.assertEqual(order.items.get().status, "ready")

    def test_a_drink_sent_to_the_bar_counts_as_made_at_once(self):
        # Sent a minute ago to a bar: poured, so cancelling it is wastage.
        order = self.order(status="open", item_status="sent", station=self.bar, sent_ago=timedelta(minutes=1))
        resp = self.void("cashier", order)
        self.assertNeedsApproval(resp)

    def test_a_dish_just_sent_to_the_kitchen_is_still_a_plain_cancel(self):
        order = self.order(status="open", item_status="sent", sent_ago=timedelta(minutes=1))
        resp = self.void("cashier", order)
        self.assertEqual(resp.status_code, 200, resp.content)


class WithAPinTest(Base):
    """The same changes go through with a manager's PIN, and are recorded."""

    def test_a_pin_approves_the_void_and_the_audit_shows_it(self):
        order = self.order()
        resp = self.void("cashier", order, pin=PIN)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.bill(order), D("0.00"))
        event = OrderEvent.objects.get(order=order, event_type="changed_after_bill")
        self.assertEqual(
            (event.metadata["before"], event.metadata["after"], event.metadata["approved_by"], event.created_by),
            ("1050.00", "0.00", "ap_manager", self.users["cashier"]))
        void = OrderEvent.objects.get(order=order, event_type="item_voided")
        self.assertEqual(void.metadata["approved_by"], "ap_manager")

        today = get_business_date(outlet=self.outlet)        # not the calendar date: after midnight they differ
        row = discount_void_audit(self.tenant, self.outlet, today, today)["after_bill"]
        self.assertEqual(len(row), 1)
        self.assertEqual((row[0]["taken_off"], row[0]["by"], row[0]["approved_by"]),
                         (D("1050.00"), "ap_cashier", "ap_manager"))

    def test_a_cut_on_a_printed_bill_is_on_the_void_audit(self):
        # L1b was logged as "item_updated", which the audit doesn't count.
        order = self.order(quantity=3)
        resp = self.post("cashier", reverse("reduce-item", args=[self.item.id]),
                         {"reduce_by": 2, "reason": "Wrong order"}, pin=PIN)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.bill(order), D("1050.00"))
        today = get_business_date(outlet=self.outlet)
        audit = discount_void_audit(self.tenant, self.outlet, today, today)
        self.assertEqual(audit["voids"], [{"created_by__username": "ap_cashier", "count": 1}])
        self.assertEqual(audit["after_bill"][0]["what"], "Took 2 x Biryani off")

    def test_a_manager_needs_no_pin_and_is_recorded_as_approving(self):
        order = self.order()
        resp = self.post("manager", reverse("apply-discount", args=[order.id]),
                         {"type": "percentage", "value": 10, "reason": "Regular guest"})
        self.assertEqual(resp.status_code, 200, resp.content)
        event = OrderEvent.objects.get(order=order, event_type="changed_after_bill")
        self.assertEqual(event.metadata["approved_by"], "ap_manager")

    def test_a_manager_can_now_give_a_dish_discount_on_a_printed_bill(self):
        order = self.order()
        resp = self.post("manager", reverse("item-discount", args=[self.item.id]), {"percent": 10, "reason": "Cold"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.bill(order), D("945.00"))

    def test_cancelling_the_whole_printed_bill_is_one_audit_row(self):
        order = self.order()
        resp = self.post("cashier", reverse("cancel-order", args=[order.id]), {"reason": "Guest left"}, pin=PIN)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(OrderEvent.objects.filter(order=order, event_type="changed_after_bill").count(), 1)

    def test_raising_a_printed_bill_needs_nothing(self):
        order = self.order(status="billing")
        resp = self.post("cashier", reverse("toggle-parcel", args=[order.id]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(OrderEvent.objects.filter(order=order, event_type="changed_after_bill").exists())

    def test_an_open_order_is_still_a_basket(self):
        order = self.order(status="open")
        resp = self.void("cashier", order)
        self.assertEqual(resp.status_code, 200, resp.content)


class PinTest(Base):
    def test_a_wrong_pin_is_refused_and_counted(self):
        order = self.order()
        resp = self.void("cashier", order, pin="1111")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(resp.json()["refused"])
        self.assertIn("4 tries left", resp.json()["error"])
        self.users["manager"].refresh_from_db()
        self.assertEqual(self.users["manager"].approval_pin_failures, 1)
        self.assertEqual(order.items.get().status, "pending")

    def test_five_wrong_tries_lock_the_pin_even_for_the_right_one(self):
        order = self.order()
        for _ in range(approvals.PIN_TRIES):
            self.void("cashier", order, pin="1111")
        resp = self.void("cashier", order, pin=PIN)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("locked", resp.json()["error"])
        self.assertEqual(order.items.get().status, "pending")

    def test_the_lock_ends(self):
        order = self.order()
        manager = self.users["manager"]
        User.objects.filter(id=manager.id).update(approval_pin_locked_until=timezone.now() - timedelta(seconds=1))
        resp = self.void("cashier", order, pin=PIN)
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_only_a_manager_of_this_outlet_or_the_owner_can_approve(self):
        order = self.order()
        elsewhere = User.objects.create_user(username="ap_mgr2", password="pw", role="manager",
                                             tenant=self.tenant, outlet=self.other_outlet)
        approvals.set_pin(elsewhere, "8642")
        approvals.set_pin(self.users["cashier"], "9753")
        self.assertEqual(self.void("cashier", order, pin="8642", approver=elsewhere).status_code, 403)
        self.assertEqual(self.void("captain", order, pin="9753", approver=self.users["cashier"]).status_code, 403)
        approvals.set_pin(self.users["owner"], "1357")
        self.assertEqual(self.void("cashier", order, pin="1357", approver=self.users["owner"]).status_code, 200)

    def test_a_deactivated_manager_cannot_approve(self):
        order = self.order()
        User.objects.filter(id=self.users["manager"].id).update(is_active=False)
        self.assertEqual(self.void("cashier", order, pin=PIN).status_code, 403)

    def test_another_restaurants_manager_cannot_approve(self):
        order = self.order()
        other = Tenant.objects.create(name="Elsewhere")
        stranger = User.objects.create_user(username="ap_stranger", password="pw", role="owner", tenant=other)
        approvals.set_pin(stranger, "8642")
        self.assertEqual(self.void("cashier", order, pin="8642", approver=stranger).status_code, 403)

    def test_a_pin_is_stored_hashed_and_easy_ones_are_refused(self):
        manager = self.users["manager"]
        manager.refresh_from_db()
        self.assertNotIn(PIN, manager.approval_pin)
        for bad in ("123", "1234567", "12a4", "1111", "1234"):
            with self.assertRaises(approvals.PinError):
                approvals.set_pin(manager, bad)


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class PinPageTest(Base):
    def page(self, role, **post):
        client = Client()
        client.force_login(self.users[role])
        if post:
            return client.post(reverse("my_approval_pin"), post, follow=True)
        return client.get(reverse("my_approval_pin"))

    def test_a_manager_sets_their_pin_with_their_password(self):
        resp = self.page("owner", action="set", pin="4826", pin_again="4826", password="pw")
        self.assertContains(resp, "Your approval PIN is set.")
        self.users["owner"].refresh_from_db()
        self.assertTrue(self.users["owner"].approval_pin)

    def test_the_wrong_password_changes_nothing(self):
        self.page("owner", action="set", pin="4826", pin_again="4826", password="nope")
        self.users["owner"].refresh_from_db()
        self.assertEqual(self.users["owner"].approval_pin, "")

    def test_a_cashier_has_no_pin_page(self):
        self.assertNotEqual(self.page("cashier").status_code, 200)

    def test_the_owner_can_clear_a_managers_pin(self):
        client = Client()
        client.force_login(self.users["owner"])
        client.post(reverse("clear_staff_pin", args=[self.users["manager"].id]))
        self.users["manager"].refresh_from_db()
        self.assertEqual(self.users["manager"].approval_pin, "")


class BarStationTest(Base):
    def test_a_station_called_bar_counts_drinks_as_made_when_sent(self):
        from setup.views.core_views import looks_like_a_bar
        self.assertEqual([looks_like_a_bar(n) for n in ("Bar", "Main bar", "BAR counter", "Barbecue", "Kitchen")],
                         [True, True, True, False, False])


class LiveBoardQueriesTest(Base):
    """The live orders board reads each line's station (for "made as soon as
    sent"): it must come with the lines, not one query per line."""

    def queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        client = Client()
        client.force_login(self.users["manager"])
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(reverse("live-orders-data"))
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_no_query_per_line(self):
        self.order(status="open", item_status="sent", station=self.bar, sent_ago=timedelta(minutes=1))
        one = self.queries()
        for _ in range(4):
            self.order(status="open", item_status="sent", station=self.bar, sent_ago=timedelta(minutes=1))
            self.order(status="open", item_status="sent", sent_ago=timedelta(minutes=1))
        self.assertEqual(self.queries(), one)
