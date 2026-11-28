"""
A kitchen tap for a step the dish has already taken is answered as done,
and a step it cannot take is refused in kitchen words.

Why (28 Nov 2026, Tuesdays, production log): a cook's phone on a slow link
was a few seconds behind. A Ready tap on a Glenfiddich went through, the
board kept showing Ready, and the next two taps each got
"400: Invalid state constraint: Item is not 'preparing'." A Bump sent in
the same second found the dish already ready and logged it as a failure.
The cook wanted the dish ready, and it was.

kitchen.services.kitchen_service._should_move decides; nothing is written
for a step already taken (no second "is ready" message for the waiter, no
second table change).

Run: python manage.py test kitchen.test_kitchen_steps
"""
from django.urls import reverse

from kitchen.models import KitchenMessage
from kitchen.services.kitchen_service import set_item_preparing, set_item_ready, set_item_served
from kitchen.services.kot_service import create_kot
from kitchen.tests import _Base


class _Cooking(_Base):
    def setUp(self):
        super().setUp()
        create_kot(self.owner, self.order)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.status, "sent")
        self.chef_client = self._login(self.chef)

    def at(self, status):
        self.order_item.status = status
        self.order_item.save(update_fields=["status"])

    def tap(self, url_name):
        return self.chef_client.post(reverse(url_name, args=[self.order_item.id]))

    def status(self):
        self.order_item.refresh_from_db()
        return self.order_item.status


class AStepAlreadyTakenIsDoneTest(_Cooking):
    def test_ready_tapped_again_on_a_ready_dish_is_ok_and_tells_the_waiter_once(self):
        self.at("preparing")
        self.assertEqual(self.tap("mark-ready").status_code, 200)
        again = self.tap("mark-ready")
        self.assertEqual((again.status_code, again.json()), (200, {"success": True}))
        self.assertEqual(self.status(), "ready")
        self.assertEqual(KitchenMessage.objects.filter(order=self.order).count(), 1,
                         "one 'Burger is ready' for the waiter, not one per tap")

    def test_ready_on_a_dish_already_served_is_ok_and_leaves_it_served(self):
        self.at("served")
        self.assertEqual(self.tap("mark-ready").status_code, 200)
        self.assertEqual(self.status(), "served")
        self.assertFalse(KitchenMessage.objects.filter(order=self.order).exists())

    def test_start_on_a_dish_already_cooking_or_further_is_ok(self):
        for status in ("preparing", "ready", "served"):
            with self.subTest(status=status):
                self.at(status)
                self.assertEqual(self.tap("item-start").status_code, 200)
                self.assertEqual(self.status(), status, "a late Start never moves a dish back")

    def test_serve_tapped_twice_is_ok(self):
        self.at("ready")
        waiter = self._login(self.waiter)
        for _ in range(2):
            self.assertEqual(waiter.post(reverse("serve-item", args=[self.order_item.id])).status_code, 200)
        self.assertEqual(self.status(), "served")

    def test_the_steps_say_whether_the_dish_moved(self):
        self.at("preparing")
        self.assertIs(set_item_ready(self.chef, self.order_item.id), True)
        self.assertIs(set_item_ready(self.chef, self.order_item.id), False)
        self.assertIs(set_item_preparing(self.chef, self.order_item.id), False)
        self.assertIs(set_item_served(self.chef, self.order_item.id), True)
        self.assertIs(set_item_served(self.chef, self.order_item.id), False)


class BumpCountsWhatItMovedTest(_Cooking):
    def test_bump_moves_the_dishes_being_cooked_and_counts_them(self):
        self.at("preparing")
        resp = self.chef_client.post(reverse("bump-kot", args=[self.order_item.kot_id]))
        self.assertEqual(resp.json(), {"success": True, "bumped": 1})
        self.assertEqual(self.status(), "ready")

    def test_a_ready_tap_that_got_there_first_is_not_a_bump_failure(self):
        # The same second as the bump, a Ready tap already moved the dish:
        # the bump's own step finds it ready and counts nothing, no warning.
        self.at("ready")
        with self.assertNoLogs("pos.orders", level="WARNING"):
            moved = set_item_ready(self.chef, self.order_item.id)
        self.assertIs(moved, False)


class RefusedInKitchenWordsTest(_Cooking):
    def refusal(self, status, url_name, user=None):
        self.at(status)
        client = self._login(user) if user else self.chef_client
        resp = client.post(reverse(url_name, args=[self.order_item.id]))
        self.assertEqual(resp.status_code, 400, resp.content)
        return resp.json()["error"]

    def test_a_cancelled_dish(self):
        for url_name in ("item-start", "mark-ready"):
            with self.subTest(url_name):
                self.assertEqual(self.refusal("voided", url_name), "Burger was cancelled: there is nothing to do for it.")
        self.assertEqual(self.status(), "voided")

    def test_a_dish_not_sent_to_the_kitchen_yet(self):
        for status in ("pending", "review"):
            with self.subTest(status):
                self.assertEqual(self.refusal(status, "mark-ready"), "Burger has not been sent to the kitchen yet.")

    def test_ready_before_it_was_started(self):
        self.assertEqual(self.refusal("sent", "mark-ready"), "Tap Start Prep on Burger first.")
        self.assertEqual(self.status(), "sent")

    def test_serving_a_dish_still_being_made(self):
        self.assertEqual(self.refusal("preparing", "serve-item", user=self.waiter), "Burger is not ready yet.")
        self.assertEqual(self.status(), "preparing")

    def test_no_refusal_reads_like_code(self):
        words = [
            self.refusal("voided", "mark-ready"), self.refusal("pending", "item-start"),
            self.refusal("sent", "mark-ready"), self.refusal("sent", "serve-item", user=self.waiter),
        ]
        for said in words:
            self.assertNotIn("constraint", said)
            self.assertNotIn("'", said, "no status names in quotes")
