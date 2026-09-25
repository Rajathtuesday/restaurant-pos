"""
Cancelling a dish the kitchen may already have made.

Stock is taken when the ticket goes to the kitchen. Cancelling afterwards
either puts it back ("not made") or records it as wastage ("made"). Covers
the made/not-made rule (status, the 10-minute rule, the manager rule), the
ledger rows both outcomes leave behind and how the reports read them,
cancelling a whole order that has made dishes on it, the wastage report's
"from cancelled dishes" filter, a race between cancelling the order and one
of its dishes, and the start-up guard that keeps production on PostgreSQL.

Run: python manage.py test orders.tests.test_cooked_dish_cancel
"""
import csv
import io
import json
import threading
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.models import Sum
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.db_guard import require_postgres_in_production
from inventory.models import InventoryItem, InventoryTransaction, Recipe
from kitchen.models import KOTBatch
from kitchen.services.kot_service import create_kot
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem, Table
from orders.services.void_service import (
    MADE_AFTER, cancel_whole_order, kitchen_stock_hint, suggest_made,
)
from reports.services.inventory_reports import (
    cancelled_dish_wastage, inventory_usage, inventory_wastage,
)
from tenants.models import Outlet, Tenant, TenantFeatureOverride


def _world(test, name="Cooked"):
    test.tenant = Tenant.objects.create(name=f"{name} Tenant")
    test.outlet = Outlet.objects.create(tenant=test.tenant, name="Main")
    for role in ("cashier", "manager", "owner", "captain"):
        setattr(test, role, User.objects.create_user(
            username=f"{name.lower()}_{role}", password="pw", role=role,
            tenant=test.tenant, outlet=test.outlet,
        ))
    test.table = Table.objects.create(tenant=test.tenant, outlet=test.outlet, name="T7")
    cat = MenuCategory.objects.create(tenant=test.tenant, outlet=test.outlet, name="Mains")
    test.biryani = MenuItem.objects.create(
        tenant=test.tenant, outlet=test.outlet, category=cat, name="Chicken Biryani", price=Decimal("250"),
    )
    test.rice = InventoryItem.objects.create(
        tenant=test.tenant, outlet=test.outlet, name="Basmati Rice", unit="kg",
        stock=Decimal("10.000"), cost_price=Decimal("120"),
    )
    Recipe.objects.create(
        menu_item=test.biryani, inventory_item=test.rice, quantity_required=Decimal("200"), unit="g",
    )


def _order_in_kitchen(test, quantity=1, status="sent", minutes_ago=0, table=True):
    """An order whose ticket really went through create_kot, so the stock
    deduction and its "consume" ledger row are the real ones."""
    order = Order.objects.create(
        tenant=test.tenant, outlet=test.outlet, status="open",
        table=test.table if table else None,
    )
    item = OrderItem.objects.create(
        order=order, menu_item=test.biryani, quantity=quantity, price=Decimal("250"),
        gst_percentage=Decimal("0"), total_price=Decimal("250") * quantity, status="pending",
    )
    create_kot(test.cashier, order, print_on_create=False)
    if minutes_ago:
        KOTBatch.objects.filter(order=order).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
    if status != "sent":
        OrderItem.objects.filter(id=item.id).update(status=status)
    order.recalculate_totals()
    item.refresh_from_db()
    return order, item


def _post(user, name, obj_id, body=None):
    client = Client()
    client.force_login(user)
    return client.post(
        reverse(name, args=[obj_id]),
        data=json.dumps(body or {}), content_type="application/json",
    )


def _rows(item, kind):
    return list(InventoryTransaction.objects.filter(order_item=item, transaction_type=kind))


class SuggestMadeTests(SimpleTestCase):

    def test_by_status(self):
        now = timezone.now()
        self.assertFalse(suggest_made("pending", None, now))
        self.assertFalse(suggest_made("review", None, now))
        self.assertTrue(suggest_made("preparing", now, now))
        self.assertTrue(suggest_made("ready", now, now))
        self.assertTrue(suggest_made("served", now, now))

    def test_sent_turns_to_made_after_ten_minutes(self):
        now = timezone.now()
        self.assertFalse(suggest_made("sent", None, now))
        self.assertFalse(suggest_made("sent", now - MADE_AFTER + timedelta(seconds=1), now))
        self.assertTrue(suggest_made("sent", now - MADE_AFTER, now))
        self.assertEqual(MADE_AFTER, timedelta(minutes=10))


class KitchenHintTests(TestCase):

    def setUp(self):
        _world(self)

    def _hint(self, item, is_manager):
        item = OrderItem.objects.select_related("kot").get(id=item.id)
        return kitchen_stock_hint(item, is_manager)

    def test_fresh_ticket_suggests_not_made(self):
        _, item = _order_in_kitchen(self)
        self.assertEqual(self._hint(item, False), {
            "in_kitchen": True, "made_locked": False, "suggest_made": False, "restock_needs_manager": False,
        })

    def test_old_ticket_suggests_made_and_restock_needs_a_manager(self):
        _, item = _order_in_kitchen(self, minutes_ago=15)
        self.assertTrue(self._hint(item, False)["suggest_made"])
        self.assertTrue(self._hint(item, False)["restock_needs_manager"])
        self.assertFalse(self._hint(item, True)["restock_needs_manager"])

    def test_ready_is_locked_as_made(self):
        _, item = _order_in_kitchen(self, status="ready")
        hint = self._hint(item, False)
        self.assertTrue(hint["made_locked"])
        self.assertTrue(hint["suggest_made"])
        self.assertFalse(hint["restock_needs_manager"])

    def test_live_board_sends_the_hints(self):
        _order_in_kitchen(self, minutes_ago=15)
        client = Client()
        client.force_login(self.cashier)
        data = client.get(reverse("live-orders-data")).json()
        line = data["orders"][0]["items"][0]
        self.assertTrue(line["suggest_made"])
        self.assertTrue(line["restock_needs_manager"])
        self.assertFalse(line["made_locked"])

    def test_running_order_sends_the_hints(self):
        order, _ = _order_in_kitchen(self, status="ready")
        client = Client()
        client.force_login(self.cashier)
        data = client.get(reverse("running-order-data", args=[order.id])).json()
        self.assertTrue(data["items"][0]["made_locked"])


class CancelDishStockTests(TestCase):

    def setUp(self):
        _world(self)

    def _stock(self):
        self.rice.refresh_from_db()
        return self.rice.stock

    def test_ticket_deducted_the_rice(self):
        _order_in_kitchen(self)
        self.assertEqual(self._stock(), Decimal("9.800"))

    def test_fresh_ticket_puts_stock_back_by_default(self):
        order, item = _order_in_kitchen(self)

        resp = _post(self.cashier, "cancel-item", item.id, {"reason": "Wrong order"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._stock(), Decimal("10.000"))
        back = _rows(item, "consume")
        self.assertEqual([r.quantity for r in back], [Decimal("0.200")])
        self.assertIn("cancelled before cooking", back[0].reference)
        self.assertEqual(_rows(item, "wastage"), [])
        event = OrderEvent.objects.filter(order=order, event_type="item_voided").latest("id")
        self.assertEqual(event.metadata["stock"], "returned")

    def test_made_keeps_stock_down_and_records_wastage(self):
        order, item = _order_in_kitchen(self)

        resp = _post(self.cashier, "cancel-item", item.id, {"reason": "Customer changed mind", "made": True})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._stock(), Decimal("9.800"))
        self.assertEqual([r.quantity for r in _rows(item, "consume")], [Decimal("0.200")])
        wasted = _rows(item, "wastage")
        self.assertEqual([r.quantity for r in wasted], [Decimal("-0.200")])
        self.assertIn("Customer changed mind", wasted[0].reference)
        event = OrderEvent.objects.filter(order=order, event_type="item_voided").latest("id")
        self.assertEqual(event.metadata["stock"], "wasted")

    def test_old_ticket_counts_as_made_by_default(self):
        _, item = _order_in_kitchen(self, minutes_ago=15)

        resp = _post(self.cashier, "cancel-item", item.id, {"reason": "Kitchen issue"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._stock(), Decimal("9.800"))
        self.assertEqual(len(_rows(item, "wastage")), 1)

    def test_cashier_cannot_put_back_a_dish_the_kitchen_probably_made(self):
        _, item = _order_in_kitchen(self, minutes_ago=15)

        resp = _post(self.cashier, "cancel-item", item.id, {"reason": "x", "made": False})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("manager", resp.json()["error"].lower())
        item.refresh_from_db()
        self.assertEqual(item.status, "sent")
        self.assertEqual(self._stock(), Decimal("9.800"))
        self.assertFalse(InventoryTransaction.objects.filter(order_item=item).exists())

    def test_manager_can_put_it_back(self):
        _, item = _order_in_kitchen(self, minutes_ago=15)

        resp = _post(self.manager, "cancel-item", item.id, {"reason": "Never started", "made": False})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._stock(), Decimal("10.000"))

    def test_cooking_dish_needs_a_manager_to_restock(self):
        _, item = _order_in_kitchen(self, status="preparing")
        self.assertEqual(_post(self.captain, "cancel-item", item.id, {"reason": "x", "made": False}).status_code, 400)
        self.assertEqual(_post(self.owner, "cancel-item", item.id, {"reason": "x", "made": False}).status_code, 200)
        self.assertEqual(self._stock(), Decimal("10.000"))

    def test_ready_dish_can_never_go_back_to_stock(self):
        _, item = _order_in_kitchen(self, status="ready")

        resp = _post(self.manager, "cancel-item", item.id, {"reason": "x", "made": False})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("already made", resp.json()["error"])
        self.assertEqual(self._stock(), Decimal("9.800"))

    def test_anyone_can_cancel_a_ready_dish_as_wastage(self):
        _, item = _order_in_kitchen(self, status="ready")

        resp = _post(self.captain, "cancel-item", item.id, {"reason": "Went cold"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._stock(), Decimal("9.800"))
        self.assertEqual(len(_rows(item, "wastage")), 1)

    def test_dish_not_sent_yet_moves_no_stock(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open")
        item = OrderItem.objects.create(
            order=order, menu_item=self.biryani, quantity=1, price=Decimal("250"),
            gst_percentage=Decimal("0"), total_price=Decimal("250"), status="pending",
        )

        resp = _post(self.cashier, "cancel-item", item.id, {"made": True})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._stock(), Decimal("10.000"))
        self.assertFalse(InventoryTransaction.objects.filter(order_item=item).exists())

    def test_made_must_be_true_or_false(self):
        _, item = _order_in_kitchen(self)
        for bad in ("yes", 1, "true"):
            resp = _post(self.cashier, "cancel-item", item.id, {"reason": "x", "made": bad})
            self.assertEqual(resp.status_code, 400, bad)
        item.refresh_from_db()
        self.assertEqual(item.status, "sent")

    def test_reduce_one_of_two_as_wastage(self):
        order, item = _order_in_kitchen(self, quantity=2)
        self.assertEqual(self._stock(), Decimal("9.600"))

        resp = _post(self.cashier, "reduce-item", item.id, {"reduce_by": 1, "reason": "Too much", "made": True})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._stock(), Decimal("9.600"))
        voided = OrderItem.objects.get(order=order, status="voided")
        self.assertEqual([r.quantity for r in _rows(voided, "wastage")], [Decimal("-0.200")])
        self.assertFalse(InventoryTransaction.objects.filter(order_item=item).exists())

    def test_reduce_refuses_a_cashier_restock_before_splitting(self):
        order, item = _order_in_kitchen(self, quantity=2, status="preparing")

        resp = _post(self.cashier, "reduce-item", item.id, {"reduce_by": 1, "reason": "x", "made": False})

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(OrderItem.objects.filter(order=order, status="voided").exists())
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)


class LedgerAndReportTests(TestCase):
    """Both outcomes must leave "used for sales" at zero for a dish that was
    never sold, so food cost and the variance report stay honest."""

    def setUp(self):
        _world(self)
        self.day = timezone.localdate()

    def _net(self, kind):
        return InventoryTransaction.objects.filter(
            item=self.rice, transaction_type=kind,
        ).aggregate(t=Sum("quantity"))["t"] or Decimal("0")

    def test_returned_dish_nets_usage_to_zero(self):
        _, item = _order_in_kitchen(self)
        _post(self.cashier, "cancel-item", item.id, {"reason": "x", "made": False})
        self.assertEqual(self._net("consume"), Decimal("0"))
        self.assertEqual(self._net("wastage"), Decimal("0"))

    def test_wasted_dish_moves_its_usage_to_wastage(self):
        _, item = _order_in_kitchen(self)
        _post(self.cashier, "cancel-item", item.id, {"reason": "x", "made": True})
        self.assertEqual(self._net("consume"), Decimal("0"))
        self.assertEqual(self._net("wastage"), Decimal("-0.200"))
        self.rice.refresh_from_db()
        # Opening 10 - used 0 - wasted 0.2 = 9.8 on the shelf.
        self.assertEqual(self.rice.stock, Decimal("10.000") + self._net("consume") + self._net("wastage"))

    def test_usage_report_does_not_count_the_cancelled_dish(self):
        _, item = _order_in_kitchen(self)
        _post(self.cashier, "cancel-item", item.id, {"reason": "x", "made": True})
        usage = {r["item__name"]: r["total_qty"] for r in inventory_usage(self.tenant, self.outlet, self.day, self.day)}
        # Used and then un-used the same day nets to nothing, so it isn't listed.
        self.assertNotIn("Basmati Rice", usage)

    def test_variance_report_shows_no_gap_for_a_cancelled_dish(self):
        order, item = _order_in_kitchen(self, status="preparing")
        cancel_whole_order(self.manager, order.id, reason="Guest left", include_made=True)
        client = Client()
        client.force_login(self.manager)
        resp = client.get(reverse("inventory_variance"), {"date": self.day.isoformat(), "export": "csv"})
        self.assertEqual(resp.status_code, 200)
        rows = list(csv.DictReader(io.StringIO(resp.content.decode())))
        rice = next(r for r in rows if r["Item"] == "Basmati Rice")
        self.assertEqual(rice["Recipe Expected"], "0.000")
        self.assertEqual(rice["Txn Consumed"], "0.000")
        self.assertEqual(rice["Variance"], "0.000")
        self.assertEqual(rice["Wastage"], "0.200")

    def _manual_wastage(self, qty):
        return InventoryTransaction.objects.create(
            tenant=self.tenant, outlet=self.outlet, item=self.rice,
            transaction_type="wastage", quantity=qty, reference="Spilled",
        )

    def test_wastage_filter_splits_cancelled_from_manual(self):
        _, item = _order_in_kitchen(self)
        _post(self.cashier, "cancel-item", item.id, {"reason": "Burnt", "made": True})
        self._manual_wastage(Decimal("-0.500"))

        def total(source):
            rows = inventory_wastage(self.tenant, self.outlet, self.day, self.day, source)
            return sum((r["total_qty"] for r in rows), Decimal("0")), sum((r["total_cost"] for r in rows), Decimal("0"))

        self.assertEqual(total("all"), (Decimal("0.700"), Decimal("84.000")))
        self.assertEqual(total("cancelled"), (Decimal("0.200"), Decimal("24.000")))
        self.assertEqual(total("manual"), (Decimal("0.500"), Decimal("60.000")))

    def test_cancelled_dish_list(self):
        order, item = _order_in_kitchen(self, quantity=2)
        _post(self.captain, "cancel-item", item.id, {"reason": "Burnt", "made": True})

        rows = cancelled_dish_wastage(self.tenant, self.outlet, self.day, self.day)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["where"], "T7")
        self.assertEqual(row["order_id"], order.id)
        self.assertEqual(row["dish"], "Chicken Biryani")
        self.assertEqual(row["quantity"], 2)
        self.assertEqual(row["reason"], "Burnt")
        self.assertEqual(row["staff"], "cooked_captain")
        self.assertEqual(row["cost"], Decimal("48.000"))

    def test_wastage_report_only_shows_this_outlet(self):
        _, item = _order_in_kitchen(self)
        _post(self.cashier, "cancel-item", item.id, {"reason": "Burnt", "made": True})
        other = Outlet.objects.create(tenant=self.tenant, name="Branch")
        other_tenant = Tenant.objects.create(name="Someone Else")
        other_tenant_outlet = Outlet.objects.create(tenant=other_tenant, name="Main")
        for t, o in ((self.tenant, other), (other_tenant, other_tenant_outlet)):
            self.assertEqual(inventory_wastage(t, o, self.day, self.day, "cancelled"), [])
            self.assertEqual(cancelled_dish_wastage(t, o, self.day, self.day), [])

    def test_inventory_report_page_filters(self):
        _, item = _order_in_kitchen(self)
        _post(self.cashier, "cancel-item", item.id, {"reason": "Burnt", "made": True})
        self._manual_wastage(Decimal("-0.500"))
        client = Client()
        client.force_login(self.owner)

        resp = client.get(reverse("inventory_report"), {"wastage_source": "cancelled", "tab": "wastage"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["wastage_source"], "cancelled")
        self.assertEqual(len(resp.context["cancelled_dishes"]), 1)
        self.assertEqual(resp.context["total_wastage_cost"], Decimal("24.000"))
        self.assertContains(resp, "Dishes cancelled after the kitchen made them")

        resp = client.get(reverse("inventory_report"), {"wastage_source": "manual"})
        self.assertEqual(resp.context["cancelled_dishes"], [])
        self.assertEqual(resp.context["total_wastage_cost"], Decimal("60.000"))

        resp = client.get(reverse("inventory_report"), {"wastage_source": "<script>"})
        self.assertEqual(resp.context["wastage_source"], "all")


class CancelWholeOrderTests(TestCase):

    def setUp(self):
        _world(self)

    def _stock(self):
        self.rice.refresh_from_db()
        return self.rice.stock

    def _add_line(self, order, status, minutes_ago=0):
        item = OrderItem.objects.create(
            order=order, menu_item=self.biryani, quantity=1, price=Decimal("250"),
            gst_percentage=Decimal("0"), total_price=Decimal("250"), status="pending",
        )
        create_kot(self.cashier, order, print_on_create=False)
        item.refresh_from_db()
        if minutes_ago:
            KOTBatch.objects.filter(id=item.kot_id).update(created_at=timezone.now() - timedelta(minutes=minutes_ago))
        OrderItem.objects.filter(id=item.id).update(status=status)
        return item

    def test_nothing_made_cancels_straight_away(self):
        order, item = _order_in_kitchen(self)

        resp = _post(self.cashier, "cancel-order", order.id, {})

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertIsNotNone(order.closed_at)
        self.assertEqual(self._stock(), Decimal("10.000"))
        self.table.refresh_from_db()
        self.assertEqual(self.table.state, "free")
        event = OrderEvent.objects.get(order=order, event_type="order_cancelled")
        self.assertEqual(event.metadata, {"reason": "Order cancelled", "dishes": 1, "already_made": 0})

    def test_made_dish_asks_first_and_changes_nothing(self):
        order, fresh = _order_in_kitchen(self)
        cooking = self._add_line(order, "preparing")

        resp = _post(self.cashier, "cancel-order", order.id, {"reason": "Guest left"})

        self.assertEqual(resp.status_code, 409)
        data = resp.json()
        self.assertTrue(data["needs_confirm"])
        self.assertFalse(data["needs_manager"])
        self.assertEqual(data["made_items"], [{"name": "Chicken Biryani", "quantity": 1, "status": "preparing"}])
        self.assertIn("1 dish on this order was already made", data["error"])
        order.refresh_from_db()
        self.assertEqual(order.status, "open")
        self.assertFalse(OrderItem.objects.filter(order=order, status="voided").exists())
        self.assertEqual(self._stock(), Decimal("9.600"))

    def test_confirming_needs_a_reason(self):
        order, _ = _order_in_kitchen(self, status="ready")

        resp = _post(self.cashier, "cancel-order", order.id, {"include_made": True, "reason": "  "})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.json()["error"])
        order.refresh_from_db()
        self.assertEqual(order.status, "open")

    def test_include_made_must_be_a_real_true(self):
        order, _ = _order_in_kitchen(self, status="ready")
        resp = _post(self.cashier, "cancel-order", order.id, {"include_made": "true", "reason": "x"})
        self.assertEqual(resp.status_code, 409)

    def test_confirmed_cancel_leaves_nothing_behind(self):
        order, fresh = _order_in_kitchen(self)
        cooking = self._add_line(order, "preparing")
        old = self._add_line(order, "sent", minutes_ago=20)
        self.assertEqual(self._stock(), Decimal("9.400"))

        resp = _post(self.cashier, "cancel-order", order.id, {"include_made": True, "reason": "Guest left"})

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(OrderItem.objects.filter(order=order).exclude(status="voided").exists())
        # The fresh ticket's rice goes back; the cooking and the old one are wasted.
        self.assertEqual(self._stock(), Decimal("9.600"))
        self.assertEqual(_rows(fresh, "wastage"), [])
        self.assertEqual(len(_rows(cooking, "wastage")), 1)
        self.assertEqual(len(_rows(old, "wastage")), 1)
        order.refresh_from_db()
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(order.grand_total, Decimal("0"))
        event = OrderEvent.objects.get(order=order, event_type="order_cancelled")
        self.assertEqual(event.metadata["already_made"], 2)
        self.assertEqual(event.metadata["reason"], "Guest left")
        self.assertEqual(OrderEvent.objects.filter(order=order, event_type="item_voided").count(), 3)

    def test_served_dish_needs_a_manager_even_when_confirmed(self):
        order, item = _order_in_kitchen(self, status="served")

        resp = _post(self.captain, "cancel-order", order.id, {"include_made": True, "reason": "Complaint"})

        self.assertEqual(resp.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.status, "served")
        self.assertEqual(_post(self.owner, "cancel-order", order.id, {"include_made": True, "reason": "Complaint"}).status_code, 200)

    def test_other_tenants_order_is_404_and_untouched(self):
        order, item = _order_in_kitchen(self)
        stranger_tenant = Tenant.objects.create(name="Stranger")
        stranger_outlet = Outlet.objects.create(tenant=stranger_tenant, name="Main")
        stranger = User.objects.create_user(
            username="stranger_owner", password="pw", role="owner", tenant=stranger_tenant, outlet=stranger_outlet,
        )

        resp = _post(stranger, "cancel-order", order.id, {"include_made": True, "reason": "x"})

        self.assertEqual(resp.status_code, 404)
        order.refresh_from_db()
        self.assertEqual(order.status, "open")

    def test_other_outlet_is_404(self):
        order, _ = _order_in_kitchen(self)
        branch = Outlet.objects.create(tenant=self.tenant, name="Branch")
        branch_manager = User.objects.create_user(
            username="branch_manager", password="pw", role="manager", tenant=self.tenant, outlet=branch,
        )
        self.assertEqual(_post(branch_manager, "cancel-order", order.id, {}).status_code, 404)

    def test_cancelled_order_cannot_be_cancelled_again(self):
        order, _ = _order_in_kitchen(self)
        self.assertEqual(_post(self.cashier, "cancel-order", order.id, {}).status_code, 200)
        self.assertEqual(_post(self.cashier, "cancel-order", order.id, {}).status_code, 400)
        self.assertEqual(self._stock(), Decimal("10.000"))

    def test_waiter_role_cannot_cancel_orders(self):
        order, _ = _order_in_kitchen(self)
        waiter = User.objects.create_user(
            username="cooked_waiter", password="pw", role="waiter", tenant=self.tenant, outlet=self.outlet,
        )
        resp = _post(waiter, "cancel-order", order.id, {})
        self.assertIn(resp.status_code, (302, 403))
        order.refresh_from_db()
        self.assertEqual(order.status, "open")


class CancelOrderConcurrencyTests(TransactionTestCase):
    """Cancelling the order while someone cancels one of its dishes: each
    dish is cancelled once and its stock moved once, whichever lands first."""

    def setUp(self):
        _world(self, name="CookedRace")

    def _race(self, calls):
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

    def test_cancel_order_and_cancel_dish_at_once(self):
        order, item = _order_in_kitchen(self, quantity=3)

        results = self._race([
            (self.cashier, reverse("cancel-order", args=[order.id]), {"reason": "Guest left"}),
            (self.captain, reverse("cancel-item", args=[item.id]), {"reason": "Wrong", "made": False}),
        ])

        self.assertTrue(all(isinstance(r, int) for r in results), results)
        self.assertNotIn(500, results, results)
        item.refresh_from_db()
        self.assertEqual(item.status, "voided")
        self.rice.refresh_from_db()
        self.assertEqual(self.rice.stock, Decimal("10.000"))  # returned once, not twice
        returned = InventoryTransaction.objects.filter(order_item=item, transaction_type="consume")
        self.assertEqual(returned.count(), 1)

    def test_kitchen_starts_cooking_while_the_order_is_cancelled(self):
        """The likeliest real collision: the chef taps "start" on the kitchen
        screen just as the cashier cancels the order. Before every path
        locked the order first, this could deadlock and one side got a 500."""
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="kitchen_display", enabled=True)
        chef = User.objects.create_user(
            username="cookedrace_chef", password="pw", role="chef", tenant=self.tenant, outlet=self.outlet,
        )
        for _ in range(3):
            order, item = _order_in_kitchen(self)

            results = self._race([
                (self.cashier, reverse("cancel-order", args=[order.id]), {"reason": "Guest left"}),
                (chef, reverse("item-start", args=[item.id]), {}),
            ])

            self.assertTrue(all(isinstance(r, int) for r in results), results)
            self.assertNotIn(500, results, results)
            order.refresh_from_db()
            item.refresh_from_db()
            if results[0] == 200:
                # Cancel won: the dish was cancelled before cooking started.
                self.assertEqual((order.status, item.status), ("cancelled", "voided"))
                self.assertEqual(results[1], 400)
            else:
                # The kitchen won: the dish is cooking, so the cancel asks first.
                self.assertEqual(results, [409, 200])
                self.assertEqual((order.status, item.status), ("open", "preparing"))

    def test_two_staff_cancel_the_same_order(self):
        order, item = _order_in_kitchen(self, quantity=2, status="ready")

        results = self._race([
            (self.manager, reverse("cancel-order", args=[order.id]), {"include_made": True, "reason": "a"}),
            (self.owner, reverse("cancel-order", args=[order.id]), {"include_made": True, "reason": "b"}),
        ])

        self.assertEqual(sorted(results), [200, 400], results)
        self.assertEqual(InventoryTransaction.objects.filter(order_item=item, transaction_type="wastage").count(), 1)
        self.assertEqual(OrderEvent.objects.filter(order=order, event_type="order_cancelled").count(), 1)


class ProductionDatabaseGuardTests(SimpleTestCase):

    def test_production_on_sqlite_refuses_to_start(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            require_postgres_in_production("django.db.backends.sqlite3", debug=False)
        self.assertIn("PostgreSQL", str(ctx.exception))

    def test_missing_engine_refuses_to_start(self):
        with self.assertRaises(ImproperlyConfigured):
            require_postgres_in_production(None, debug=False)

    def test_allowed_cases(self):
        require_postgres_in_production("django.db.backends.postgresql", debug=False)
        require_postgres_in_production("django.db.backends.sqlite3", debug=True)
        require_postgres_in_production("django.db.backends.sqlite3", debug=False, allow_other=True)
