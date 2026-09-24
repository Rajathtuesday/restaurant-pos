"""
Taking units off a live order line ("make that one naan, not two").

Covers void_service.reduce_item_quantity and the reduce-item endpoint:
the split into an active line plus a voided line, stock put back for only
the removed units, the served-item manager rule, tenant/outlet isolation,
exact totals in both GST modes, what the kitchen and reports see, and real
concurrent requests racing on the same line.

Run: python manage.py test orders.tests.test_reduce_quantity
"""
import json
import threading
from decimal import Decimal

from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import InventoryItem, ModifierRecipe, Recipe
from kitchen.services.kot_service import create_kot
from menu.models import MenuCategory, MenuItem, Modifier, ModifierGroup
from orders.models import Order, OrderEvent, OrderItem, OrderItemModifier, Table
from orders.services.void_service import reduce_item_quantity
from tenants.models import Outlet, Tenant


def _world(test, name="Reduce"):
    test.tenant = Tenant.objects.create(name=f"{name} Tenant")
    test.outlet = Outlet.objects.create(tenant=test.tenant, name="Main")
    test.cashier = User.objects.create_user(
        username=f"{name.lower()}_cashier", password="pw", role="cashier",
        tenant=test.tenant, outlet=test.outlet,
    )
    test.manager = User.objects.create_user(
        username=f"{name.lower()}_manager", password="pw", role="manager",
        tenant=test.tenant, outlet=test.outlet,
    )
    test.table = Table.objects.create(tenant=test.tenant, outlet=test.outlet, name="T4")
    cat = MenuCategory.objects.create(tenant=test.tenant, outlet=test.outlet, name="Breads")
    test.naan = MenuItem.objects.create(
        tenant=test.tenant, outlet=test.outlet, category=cat, name="Butter Naan", price=Decimal("60"),
    )
    test.flour = InventoryItem.objects.create(
        tenant=test.tenant, outlet=test.outlet, name="Flour", unit="kg", stock=Decimal("10.000"),
    )
    Recipe.objects.create(
        menu_item=test.naan, inventory_item=test.flour, quantity_required=Decimal("500"), unit="g",
    )


def _sent_line(test, quantity=2, status="sent", order_status="open", table=True):
    order = Order.objects.create(
        tenant=test.tenant, outlet=test.outlet, status=order_status,
        table=test.table if table else None,
    )
    item = OrderItem.objects.create(
        order=order, menu_item=test.naan, quantity=quantity, price=Decimal("60"),
        gst_percentage=Decimal("0"), total_price=Decimal("60") * quantity, status=status,
    )
    if status not in ("pending", "review"):
        # The deduction that happened when the ticket went to the kitchen.
        test.flour.stock -= Decimal("0.5") * quantity
        test.flour.save(update_fields=["stock"])
    order.recalculate_totals()
    return order, item


class ReduceQuantityTests(TestCase):

    def setUp(self):
        _world(self)

    def _post(self, user, item_id, body):
        client = Client()
        client.force_login(user)
        return client.post(
            reverse("reduce-item", args=[item_id]),
            data=json.dumps(body), content_type="application/json",
        )

    # ── the split ────────────────────────────────────────────────────────

    def test_sent_line_is_split_into_active_and_voided(self):
        order, item = _sent_line(self, quantity=2)

        resp = self._post(self.cashier, item.id, {"reduce_by": 1, "reason": "Customer changed mind"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["remaining"], 1)
        item.refresh_from_db()
        self.assertEqual((item.quantity, item.total_price, item.status), (1, Decimal("60"), "sent"))

        voided = OrderItem.objects.get(order=order, status="voided")
        self.assertEqual(voided.quantity, 1)
        self.assertEqual(voided.total_price, Decimal("60"))
        self.assertEqual(voided.void_reason, "Customer changed mind")
        self.assertEqual(voided.voided_by, self.cashier)
        self.assertEqual(voided.menu_item, self.naan)

        order.refresh_from_db()
        self.assertEqual(order.grand_total, Decimal("60"))
        self.assertEqual(resp.json()["new_total"], 60.0)

    def test_stock_comes_back_for_only_the_removed_units(self):
        _, item = _sent_line(self, quantity=3)
        self.assertEqual(self.flour.stock, Decimal("8.500"))

        reduce_item_quantity(self.cashier, item.id, 1, "Wrong order")

        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("9.000"))

    def test_modifiers_follow_the_voided_units(self):
        cheese = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Cheese", unit="g", stock=Decimal("440"),
        )
        group = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extras")
        mod = Modifier.objects.create(group=group, name="Extra Cheese", price=Decimal("20"))
        ModifierRecipe.objects.create(modifier=mod, inventory_item=cheese, quantity_required=Decimal("30"), unit="g")
        order, item = _sent_line(self, quantity=2)
        item.total_price = Decimal("160")  # (60 + 20) x 2
        item.save(update_fields=["total_price"])
        OrderItemModifier.objects.create(order_item=item, modifier=mod, name=mod.name, price=mod.price)

        reduce_item_quantity(self.cashier, item.id, 1, "Out of stock")

        item.refresh_from_db()
        voided = OrderItem.objects.get(order=order, status="voided")
        self.assertEqual(item.total_price, Decimal("80"))
        self.assertEqual(voided.total_price, Decimal("80"))
        self.assertEqual(list(voided.modifiers.values_list("name", "price")), [("Extra Cheese", Decimal("20"))])
        self.assertEqual(item.modifiers.count(), 1)
        cheese.refresh_from_db()
        self.assertEqual(cheese.stock, Decimal("470"))  # 30 g back for the one removed naan only

    def test_line_not_yet_sent_is_just_a_basket_edit(self):
        order, item = _sent_line(self, quantity=3, status="pending")
        stock_before = self.flour.stock

        reduce_item_quantity(self.cashier, item.id, 1, "")

        item.refresh_from_db()
        self.assertEqual((item.quantity, item.total_price), (2, Decimal("120")))
        self.assertFalse(OrderItem.objects.filter(order=order, status="voided").exists())
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, stock_before)
        event = OrderEvent.objects.get(order=order, event_type="item_updated")
        self.assertEqual(event.metadata["action"], "quantity_reduced")
        self.assertFalse(OrderEvent.objects.filter(order=order, event_type="item_voided").exists())

    def test_removing_everything_voids_the_line(self):
        order, item = _sent_line(self, quantity=1)

        resp = self._post(self.cashier, item.id, {"reduce_by": 1, "reason": "Wrong order"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["remaining"], 0)
        item.refresh_from_db()
        self.assertEqual(item.status, "voided")
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 1)
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))

    def test_partial_reduction_is_on_the_void_audit(self):
        order, item = _sent_line(self, quantity=2)

        reduce_item_quantity(self.cashier, item.id, 1, "Kitchen issue")

        event = OrderEvent.objects.get(order=order, event_type="item_voided")
        self.assertEqual(event.metadata["reason"], "Kitchen issue")
        self.assertEqual(event.metadata["quantity"], 1)
        self.assertTrue(event.metadata["partial"])
        self.assertEqual(event.created_by, self.cashier)

    # ── refusals ─────────────────────────────────────────────────────────

    def test_cannot_remove_more_than_is_left(self):
        _, item = _sent_line(self, quantity=2)

        resp = self._post(self.cashier, item.id, {"reduce_by": 3})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("Only 2 left", resp.json()["error"])
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_bad_amounts_are_refused(self):
        _, item = _sent_line(self, quantity=2)
        for bad in (0, -1, "abc", None, 1.5, "1.5", True, [1]):
            with self.subTest(reduce_by=bad):
                resp = self._post(self.cashier, item.id, {"reduce_by": bad})
                self.assertEqual(resp.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)
        self.assertFalse(OrderItem.objects.filter(order=item.order, status="voided").exists())

    def test_whole_number_forms_are_accepted(self):
        _, item = _sent_line(self, quantity=5)
        for ok in (1, 1.0, "1", " 1 "):
            with self.subTest(reduce_by=ok):
                self.assertEqual(self._post(self.cashier, item.id, {"reduce_by": ok}).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 1)

    def test_served_line_needs_a_manager(self):
        _, item = _sent_line(self, quantity=2, status="served")

        resp = self._post(self.cashier, item.id, {"reduce_by": 1, "reason": "x"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Manager override", resp.json()["error"])

        resp = self._post(self.manager, item.id, {"reduce_by": 1, "reason": "x"})
        self.assertEqual(resp.status_code, 200)

    def test_paid_order_is_locked(self):
        _, item = _sent_line(self, quantity=2, order_status="paid", table=False)

        resp = self._post(self.manager, item.id, {"reduce_by": 1})

        self.assertEqual(resp.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_waiter_and_kitchen_roles_cannot_reduce(self):
        _, item = _sent_line(self, quantity=2)
        for role in ("waiter", "chef", "kitchen"):
            with self.subTest(role=role):
                user = User.objects.create_user(
                    username=f"r_{role}", password="pw", role=role, tenant=self.tenant, outlet=self.outlet,
                )
                self.assertEqual(self._post(user, item.id, {"reduce_by": 1}).status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_get_is_not_allowed(self):
        _, item = _sent_line(self, quantity=2)
        client = Client()
        client.force_login(self.cashier)
        self.assertEqual(client.get(reverse("reduce-item", args=[item.id])).status_code, 405)

    def test_not_logged_in_is_sent_to_login(self):
        _, item = _sent_line(self, quantity=2)
        resp = Client().post(reverse("reduce-item", args=[item.id]), data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    # ── tenant / outlet isolation ────────────────────────────────────────

    def test_other_tenants_line_is_404_and_untouched(self):
        other = type("W", (), {})()
        _world(other, name="Other")
        _, their_item = _sent_line(other, quantity=2)

        resp = self._post(self.manager, their_item.id, {"reduce_by": 1})

        self.assertEqual(resp.status_code, 404)
        their_item.refresh_from_db()
        self.assertEqual(their_item.quantity, 2)
        self.assertFalse(OrderItem.objects.filter(order=their_item.order, status="voided").exists())

    def test_other_outlet_of_same_tenant_is_404(self):
        branch = Outlet.objects.create(tenant=self.tenant, name="Branch 2")
        order = Order.objects.create(tenant=self.tenant, outlet=branch, status="open")
        item = OrderItem.objects.create(
            order=order, menu_item=self.naan, quantity=2, price=Decimal("60"),
            gst_percentage=Decimal("0"), total_price=Decimal("120"), status="sent",
        )

        self.assertEqual(self._post(self.manager, item.id, {"reduce_by": 1}).status_code, 404)
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_cancel_endpoint_is_tenant_scoped_too(self):
        other = type("W", (), {})()
        _world(other, name="Other2")
        _, their_item = _sent_line(other, quantity=1)
        client = Client()
        client.force_login(self.manager)

        resp = client.post(reverse("cancel-item", args=[their_item.id]), data="{}", content_type="application/json")

        self.assertEqual(resp.status_code, 404)
        their_item.refresh_from_db()
        self.assertEqual(their_item.status, "sent")

    # ── money: reducing 3 to 2 must bill exactly like ordering 2 ────────

    def _twin_totals(self, gst_inclusive):
        self.outlet.gst_inclusive = gst_inclusive
        self.outlet.save(update_fields=["gst_inclusive"])
        dish = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.naan.category,
            name="Paneer Tikka", price=Decimal("219"), gst_percentage=Decimal("5"),
        )

        def line(qty):
            order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
            OrderItem.objects.create(
                order=order, menu_item=dish, quantity=qty, price=dish.price,
                item_discount_pct=Decimal("10"), gst_percentage=dish.gst_percentage,
                total_price=dish.price * qty, status="sent",
            )
            order.recalculate_totals()
            return order

        reduced = line(3)
        reduce_item_quantity(self.cashier, reduced.items.get().id, 1, "Wrong order")
        reduced.refresh_from_db()
        fresh = line(2)
        fresh.refresh_from_db()
        return reduced, fresh

    def test_totals_match_a_fresh_order_gst_exclusive(self):
        reduced, fresh = self._twin_totals(gst_inclusive=False)
        for field in ("subtotal", "gst_total", "discount_total", "grand_total", "round_off"):
            self.assertEqual(getattr(reduced, field), getattr(fresh, field), field)

    def test_totals_match_a_fresh_order_gst_inclusive(self):
        reduced, fresh = self._twin_totals(gst_inclusive=True)
        for field in ("subtotal", "gst_total", "discount_total", "grand_total", "round_off"):
            self.assertEqual(getattr(reduced, field), getattr(fresh, field), field)

    # ── what the kitchen, reports and reprints see ───────────────────────

    def test_kitchen_display_shows_the_new_quantity(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open", table=self.table)
        item = OrderItem.objects.create(
            order=order, menu_item=self.naan, quantity=2, price=Decimal("60"),
            gst_percentage=Decimal("0"), total_price=Decimal("120"), status="pending",
        )
        create_kot(self.manager, order)
        item.refresh_from_db()
        self.assertEqual(item.status, "sent")

        reduce_item_quantity(self.manager, item.id, 1, "Customer changed mind")

        client = Client()
        client.force_login(self.manager)
        kots = client.get(reverse("kitchen-data")).json()["kots"]
        lines = [i for k in kots for i in k["items"]]
        self.assertEqual([(i["id"], i["quantity"]) for i in lines], [(item.id, 1)])

    def test_reprinted_ticket_leaves_out_cancelled_units(self):
        from kitchen.models import KOTBatch
        from orders.services.printing_service import PrintingService
        from orders.tests.test_printing_service import BP

        order, item = _sent_line(self, quantity=2)
        kot = KOTBatch.objects.create(tenant=self.tenant, outlet=self.outlet, order=order, kot_number=7, status="confirmed")
        OrderItem.objects.filter(order=order).update(kot=kot)

        reduce_item_quantity(self.cashier, item.id, 1, "Wrong order")

        buf = BP()
        PrintingService(chars_per_line=48)._print_kot_body(buf, order, kot)
        text = buf.buf.decode("cp437", errors="replace")
        self.assertEqual(text.count("Butter Naan"), 1)
        self.assertIn("1x", text)
        self.assertNotIn("2x", text)

    def test_reports_count_only_what_was_sold(self):
        from reports.services.item_reports import top_items

        order, item = _sent_line(self, quantity=3)
        reduce_item_quantity(self.cashier, item.id, 1, "Wrong order")
        order.status = "paid"
        order.save(update_fields=["status"])

        rows = {r["menu_item__name"]: r for r in top_items(self.tenant, self.outlet)}
        self.assertEqual(rows["Butter Naan"]["total"], 2)
        self.assertEqual(rows["Butter Naan"]["total_rev"], Decimal("120"))

    def test_cancel_records_the_reason_given(self):
        _, item = _sent_line(self, quantity=1)
        client = Client()
        client.force_login(self.cashier)

        resp = client.post(
            reverse("cancel-item", args=[item.id]),
            data=json.dumps({"reason": "Out of stock"}), content_type="application/json",
        )

        self.assertEqual(resp.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.void_reason, "Out of stock")

    def test_cancel_without_a_reason_keeps_the_old_default(self):
        _, item = _sent_line(self, quantity=1)
        client = Client()
        client.force_login(self.cashier)

        self.assertEqual(client.post(reverse("cancel-item", args=[item.id])).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.void_reason, "Manual Item Cancellation")


class ReduceQuantityConcurrencyTests(TransactionTestCase):
    """
    Real simultaneous HTTP requests on one line, each thread with its own
    client and database connection. The row lock in reduce_item_quantity
    must make the outcome the same as if they had run one after another:
    no unit removed twice, no stock put back twice, totals that add up.
    """

    def setUp(self):
        _world(self, name="Race")
        self.captain = User.objects.create_user(
            username="race_captain", password="pw", role="captain",
            tenant=self.tenant, outlet=self.outlet,
        )

    def _race(self, calls):
        """calls: list of (user, url, body). Fires them all at once."""
        results = [None] * len(calls)
        barrier = threading.Barrier(len(calls))

        def run(idx, user, url, body):
            try:
                client = Client()
                client.force_login(user)
                barrier.wait()
                resp = client.post(url, data=json.dumps(body), content_type="application/json")
                results[idx] = resp.status_code
            except Exception as e:  # noqa: BLE001 -- recorded for the assertion
                results[idx] = f"ERROR: {e}"
            finally:
                connection.close()

        threads = [threading.Thread(target=run, args=(i, *c)) for i, c in enumerate(calls)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def _voided_units(self, order):
        return sum(OrderItem.objects.filter(order=order, status="voided").values_list("quantity", flat=True))

    def test_two_staff_each_remove_one_of_two(self):
        order, item = _sent_line(self, quantity=2)
        url = reverse("reduce-item", args=[item.id])

        results = self._race([
            (self.cashier, url, {"reduce_by": 1, "reason": "a"}),
            (self.captain, url, {"reduce_by": 1, "reason": "b"}),
        ])

        self.assertEqual(sorted(results), [200, 200], results)
        item.refresh_from_db()
        self.assertEqual(item.status, "voided")
        self.assertEqual(self._voided_units(order), 2)
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))  # exactly 2 naans' flour back, not 3 or 4
        order.refresh_from_db()
        self.assertEqual(order.grand_total, Decimal("0"))

    def test_two_staff_race_for_the_last_one(self):
        order, item = _sent_line(self, quantity=1)
        url = reverse("reduce-item", args=[item.id])

        results = self._race([
            (self.cashier, url, {"reduce_by": 1, "reason": "a"}),
            (self.captain, url, {"reduce_by": 1, "reason": "b"}),
        ])

        self.assertEqual(sorted(results), [200, 400], results)
        self.assertEqual(self._voided_units(order), 1)
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))  # restored once, not twice

    def test_reduce_and_cancel_at_the_same_moment(self):
        order, item = _sent_line(self, quantity=3)

        results = self._race([
            (self.cashier, reverse("reduce-item", args=[item.id]), {"reduce_by": 1, "reason": "a"}),
            (self.captain, reverse("cancel-item", args=[item.id]), {"reason": "b"}),
        ])

        self.assertNotIn(500, results, results)
        self.assertTrue(all(isinstance(r, int) for r in results), results)
        # Whichever order they landed in, all 3 units end up cancelled exactly once.
        self.assertFalse(OrderItem.objects.filter(order=order).exclude(status="voided").exists())
        self.assertEqual(self._voided_units(order), 3)
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))
        order.refresh_from_db()
        self.assertEqual(order.grand_total, Decimal("0"))

    def test_many_staff_hammer_one_line(self):
        order, item = _sent_line(self, quantity=5)
        url = reverse("reduce-item", args=[item.id])
        users = [self.cashier, self.captain, self.manager] * 3  # 9 requests for 5 units

        results = self._race([(u, url, {"reduce_by": 1, "reason": "x"}) for u in users])

        self.assertEqual(results.count(200), 5, results)
        self.assertEqual(results.count(400), 4, results)
        self.assertEqual(self._voided_units(order), 5)
        self.flour.refresh_from_db()
        self.assertEqual(self.flour.stock, Decimal("10.000"))
