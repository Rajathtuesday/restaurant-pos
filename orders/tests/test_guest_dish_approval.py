"""
Approving a guest's QR dishes one by one (21 Nov 2026).

A guest at a table orders three dishes by QR; the kitchen is out of one.
Before this, the waiter could only "Approve All" on the floor, and the one
per-dish approve (made for the pay-first token counter) never sent a
ticket, so a dine-in dish approved on its own never reached the kitchen.
A dish not accepted simply vanished from the guest's phone.

Now there is one rule (orders/services/guest_items.py): accept one, some or
all; a dine-in order's accepted dishes go to the kitchen at once, a token
order's at payment. Not accepting a dish asks for a reason, needs no PIN
for a dish the kitchen never saw, and the guest's phone says so.
Plan: md_files/eli5_qr_approve_each_dish_2026-11-21.html
"""
import json
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from kitchen.models import KOTBatch
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from orders.views.public_views import make_order_status_token
from tenants.models import Outlet, Tenant, TenantFeatureOverride
from tokens.models import TokenOrder


class _Guest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Table Pub", slug="table-pub", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        for feature in ("qr_menu", "floor_plan", "running_order"):
            TenantFeatureOverride.objects.update_or_create(tenant=self.tenant, feature=feature, defaults={"enabled": True})
        self.waiter = User.objects.create_user(username="waiter-qr", password="pw", tenant=self.tenant,
                                               outlet=self.outlet, role="waiter")
        cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.paneer = self._dish(cat, "Paneer Tikka", "220")
        self.beer = self._dish(cat, "Kingfisher", "180")
        self.brownie = self._dish(cat, "Brownie", "150")
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T4")
        self.client.force_login(self.waiter)

    def _dish(self, cat, name, price):
        return MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=cat, name=name,
                                       price=Decimal(price))

    def guest_orders(self):
        """The guest scans the table's QR and orders three dishes."""
        from django.test import Client
        guest = Client()
        resp = guest.post(reverse("create-order"), data=json.dumps({
            "table_token": str(self.table.qr_token), "source": "web",
            "cart": [{"id": self.paneer.id, "quantity": 1}, {"id": self.beer.id, "quantity": 2},
                     {"id": self.brownie.id, "quantity": 1}],
        }), content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        order = Order.objects.get(id=resp.json()["order_id"])
        self.assertEqual(set(order.items.values_list("status", flat=True)), {"review"})
        return order

    def line(self, order, dish):
        return order.items.get(menu_item=dish)

    def post(self, name, arg, body=None):
        return self.client.post(reverse(name, args=[arg]), data=json.dumps(body or {}),
                                content_type="application/json")


class ApproveEachDishTest(_Guest):

    def test_approving_one_dish_of_a_dine_in_order_sends_it_to_the_kitchen(self):
        order = self.guest_orders()
        paneer = self.line(order, self.paneer)
        resp = self.post("approve-item", paneer.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["sent_to_kitchen"])
        paneer.refresh_from_db()
        self.assertEqual(paneer.status, "sent", "approved on its own, it still reaches the kitchen")
        kot = KOTBatch.objects.get(order=order)
        self.assertEqual(list(kot.items.values_list("menu_item__name", flat=True)), ["Paneer Tikka"])
        self.assertEqual(self.line(order, self.brownie).status, "review", "the others still wait")
        self.assertEqual(resp.json()["waiting"], 2)

    def test_approving_some_dishes_in_one_step(self):
        order = self.guest_orders()
        picked = [self.line(order, self.paneer).id, self.line(order, self.beer).id]
        resp = self.post("approve-items", order.id, {"item_ids": picked})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(sorted(resp.json()["approved"]), sorted(picked))
        self.assertEqual(self.line(order, self.paneer).status, "sent")
        self.assertEqual(self.line(order, self.beer).status, "sent")
        self.assertEqual(self.line(order, self.brownie).status, "review", "not named, not approved")

    def test_approve_all_still_takes_every_waiting_dish(self):
        order = self.guest_orders()
        resp = self.post("approve-items", order.id)
        self.assertEqual(resp.json()["count"], 3)
        self.assertEqual(set(order.items.values_list("status", flat=True)), {"sent"})

    def test_a_dish_already_answered_is_refused_politely(self):
        order = self.guest_orders()
        paneer = self.line(order, self.paneer)
        self.post("approve-item", paneer.id)
        again = self.post("approve-items", order.id, {"item_ids": [paneer.id]})
        self.assertEqual(again.status_code, 400)
        self.assertIn("waiting", again.json()["error"])

    def test_item_ids_must_be_a_list_of_numbers(self):
        order = self.guest_orders()
        for bad in ({"item_ids": "all"}, {"item_ids": []}, {"item_ids": ["3"]}, {"item_ids": [True]}):
            with self.subTest(bad=bad):
                self.assertEqual(self.post("approve-items", order.id, bad).status_code, 400)
        self.assertEqual(set(order.items.values_list("status", flat=True)), {"review"}, "nothing approved")

    def test_another_orders_dish_is_not_approved_through_this_order(self):
        order = self.guest_orders()
        other = self.guest_orders_at_another_table()
        stray = self.line(other, self.brownie).id
        resp = self.post("approve-items", order.id, {"item_ids": [stray]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.line(other, self.brownie).status, "review")

    def guest_orders_at_another_table(self):
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T5")
        return self.guest_orders()


class TokenOrderWaitsForPaymentTest(_Guest):

    def token_order(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.waiter,
                                     source="counter", status="open")
        for dish in (self.paneer, self.brownie):
            OrderItem.objects.create(order=order, menu_item=dish, quantity=1, price=dish.price,
                                     gst_percentage=Decimal("5"), total_price=dish.price, status="review")
        TokenOrder.objects.create(tenant=self.tenant, outlet=self.outlet, order=order, token_number=1,
                                  date=date.today())
        return order

    def test_approving_all_of_a_token_order_sends_no_ticket_before_payment(self):
        order = self.token_order()
        resp = self.post("approve-items", order.id)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertFalse(resp.json()["sent_to_kitchen"])
        self.assertEqual(set(order.items.values_list("status", flat=True)), {"pending"})
        self.assertFalse(KOTBatch.objects.filter(order=order).exists(), "a token order's ticket goes at payment")

    def test_approving_one_dish_of_a_token_order_sends_no_ticket(self):
        order = self.token_order()
        self.post("approve-item", self.line(order, self.paneer).id)
        self.assertEqual(self.line(order, self.paneer).status, "pending")
        self.assertFalse(KOTBatch.objects.filter(order=order).exists())

    def test_the_rule_lives_on_the_order(self):
        self.assertTrue(self.token_order().pays_first)
        self.assertFalse(self.guest_orders().pays_first)


class NotAcceptedTest(_Guest):

    def test_a_waiter_does_not_accept_a_dish_with_a_reason_and_no_pin(self):
        order = self.guest_orders()
        brownie = self.line(order, self.brownie)
        before = Order.objects.get(id=order.id).grand_total
        resp = self.post("refuse-item", brownie.id, {"reason": "Not available"})
        self.assertEqual(resp.status_code, 200, resp.content)
        brownie.refresh_from_db()
        self.assertEqual(brownie.status, "voided")
        self.assertTrue(brownie.not_accepted)
        self.assertEqual(brownie.void_reason, "Not available")
        self.assertEqual(brownie.voided_by, self.waiter)
        self.assertLess(Order.objects.get(id=order.id).grand_total, before, "off the bill")
        self.assertFalse(KOTBatch.objects.filter(order=order).exists())

    def test_a_dish_already_approved_is_not_refused_here(self):
        order = self.guest_orders()
        paneer = self.line(order, self.paneer)
        self.post("approve-item", paneer.id)
        resp = self.post("refuse-item", paneer.id, {"reason": "Not available"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Remove", resp.json()["error"])
        paneer.refresh_from_db()
        self.assertEqual(paneer.status, "sent", "the kitchen has it; Remove (with its own rules) is the way")

    def test_cancelling_a_waiting_dish_from_any_screen_marks_it_not_accepted(self):
        # The token counter used cancel-item for its cross; the mark is set
        # where every cancel goes through (void_order_item), not per screen.
        order = self.guest_orders()
        owner = User.objects.create_user(username="owner-qr", password="pw", tenant=self.tenant,
                                         outlet=self.outlet, role="owner")
        self.client.force_login(owner)
        beer = self.line(order, self.beer)
        self.assertEqual(self.post("cancel-item", beer.id, {"reason": "Ordered twice"}).status_code, 200)
        beer.refresh_from_db()
        self.assertTrue(beer.not_accepted)

    def test_cancelling_a_dish_the_kitchen_has_is_not_marked_not_accepted(self):
        order = self.guest_orders()
        self.post("approve-items", order.id)
        owner = User.objects.create_user(username="owner-qr2", password="pw", tenant=self.tenant,
                                         outlet=self.outlet, role="owner")
        self.client.force_login(owner)
        paneer = self.line(order, self.paneer)
        self.post("cancel-item", paneer.id, {"reason": "Wrong order", "made": False})
        paneer.refresh_from_db()
        self.assertEqual(paneer.status, "voided")
        self.assertFalse(paneer.not_accepted)


class GuestIsToldTest(_Guest):

    def status(self, order):
        resp = self.client.get(reverse("order_status", args=[make_order_status_token(order.id)]))
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_the_guests_phone_lists_a_dish_not_accepted_with_the_reason(self):
        order = self.guest_orders()
        self.post("refuse-item", self.line(order, self.brownie).id, {"reason": "Not available"})
        data = self.status(order)
        self.assertEqual(data["not_accepted"], [{"name": "Brownie", "quantity": 1, "reason": "Not available"}])
        self.assertNotIn("Brownie", [i["name"] for i in data["items"]])

    def test_a_hand_typed_reason_stays_with_the_staff(self):
        order = self.guest_orders()
        self.post("refuse-item", self.line(order, self.brownie).id, {"reason": "guest was rude to Ravi"})
        self.assertEqual(self.status(order)["not_accepted"], [{"name": "Brownie", "quantity": 1, "reason": ""}])

    def test_every_dish_refused_says_the_order_was_not_accepted(self):
        order = self.guest_orders()
        for dish in (self.paneer, self.beer, self.brownie):
            self.post("refuse-item", self.line(order, dish).id, {"reason": "Ordered twice"})
        data = self.status(order)
        self.assertEqual(data["stage"], "not_accepted")
        self.assertEqual(len(data["not_accepted"]), 3)

    def test_a_dish_staff_removed_after_cooking_is_not_listed_as_not_accepted(self):
        order = self.guest_orders()
        self.post("approve-items", order.id)
        owner = User.objects.create_user(username="owner-qr3", password="pw", tenant=self.tenant,
                                         outlet=self.outlet, role="owner")
        self.client.force_login(owner)
        self.post("cancel-item", self.line(order, self.paneer).id, {"reason": "Wrong order", "made": False})
        self.assertEqual(self.status(order)["not_accepted"], [])


class ScreensGetWhatTheyNeedTest(_Guest):

    def test_the_floor_lists_each_waiting_guest_dish(self):
        order = self.guest_orders()
        self.post("approve-item", self.line(order, self.paneer).id)
        tables = self.client.get(reverse("tables-data")).json()["tables"]
        t4 = next(t for t in tables if t["name"] == "T4")
        self.assertEqual(t4["status"], "needs_approval")
        self.assertEqual(sorted((d["name"], d["quantity"]) for d in t4["guest_items"]),
                         [("Brownie", 1), ("Kingfisher", 2)])

    def test_the_running_order_page_offers_a_tick_and_a_cross(self):
        order = self.guest_orders()
        resp = self.client.get(reverse("running-order", args=[order.id]))
        self.assertEqual(resp.status_code, 200)
        data = resp.context["page_data"]
        self.assertTrue(data["can"]["approve"])
        self.assertEqual(data["urls"]["approve"], reverse("approve-items", args=[order.id]))
        self.assertEqual(data["urls"]["refuse"], reverse("refuse-item", args=[0]))

    def test_without_the_qr_menu_there_is_no_tick(self):
        TenantFeatureOverride.objects.filter(tenant=self.tenant, feature="qr_menu").update(enabled=False)
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.waiter, table=self.table)
        resp = self.client.get(reverse("running-order", args=[order.id]))
        self.assertFalse(resp.context["page_data"]["can"]["approve"])


class AnotherRestaurantTest(_Guest):

    def test_another_restaurants_dish_or_order_is_a_404(self):
        order = self.guest_orders()
        other_tenant = Tenant.objects.create(name="Other", slug="other-pub", tenant_type="fine_dining")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other")
        TenantFeatureOverride.objects.create(tenant=other_tenant, feature="qr_menu", enabled=True)
        stranger = User.objects.create_user(username="stranger", password="pw", tenant=other_tenant,
                                            outlet=other_outlet, role="owner")
        self.client.force_login(stranger)
        item = self.line(order, self.brownie)
        self.assertEqual(self.post("approve-items", order.id).status_code, 404)
        self.assertEqual(self.post("approve-item", item.id).status_code, 404)
        self.assertEqual(self.post("refuse-item", item.id, {"reason": "Not available"}).status_code, 404)
        self.assertEqual(set(order.items.values_list("status", flat=True)), {"review"})
