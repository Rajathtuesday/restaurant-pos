# orders/tests/test_legacy.py
#
# WHAT CHANGED AND WHY
# ====================
# Tests 4, 5, 7 all called POST /update-order-status/<id>/ which no longer exists.
# The architecture moved from order-level status transitions to item-level transitions.
#
# Test 4 — test_kitchen_preparing
#   Old: POST /update-order-status/{order.id}/  {"status": "preparing"}  → 200
#   Why dead: That URL was removed. Item status is now changed via /item-start/<item_id>/
#   Fix: Create an OrderItem with status="sent", POST to /item-start/{item.id}/,
#        verify item.status == "preparing"
#
# Test 5 — test_kitchen_ready
#   Old: POST /update-order-status/{order.id}/  {"status": "ready"}
#   Fix: Create an OrderItem with status="preparing", POST to /item-ready/{item.id}/,
#        verify item.status == "ready"
#
# Test 7 — test_payment
#   Old: POST /update-order-status/{order.id}/  {"status": "paid"}
#   Why dead: Same removed endpoint. Real payment goes through /pay/<order_id>/.
#   Fix: Use the real /pay/ endpoint with proper setup:
#        - PaymentConfig (pay_order enforces outlet-level method config)
#        - CashSession open (pay_order blocks payment if no open session)
#   Also: The view now transitions the order to "closed" (not "paid") after
#   full payment. Assertion updated accordingly.
#
# Tests 1, 2, 3, 6 were already correct — no changes.

import json
from decimal import Decimal

from django.test import Client, TestCase

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table, WaiterCall
from setup.models import PaymentConfig
from shifts.models import CashSession
from tenants.models import Outlet, Tenant


class POSTestCase(TestCase):

    def setUp(self):

        self.client = Client()

        self.tenant = Tenant.objects.create(name="Demo Restaurant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Branch")

        self.user = User.objects.create_user(
            username="owner",
            password="1234",
            tenant=self.tenant,
            outlet=self.outlet,
            role="owner",
        )

        self.client.login(username="owner", password="1234")

        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Table 1"
        )

        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Food"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Burger",
            price=100,
        )

    # ------------------------------------------------------------------
    # TEST 1 — create order  (unchanged)
    # ------------------------------------------------------------------

    def test_create_order(self):

        response = self.client.post(
            "/create-order/",
            json.dumps({"table_id": self.table.id, "cart": [{"id": self.item.id, "quantity": 2}]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data["success"])

        order = Order.objects.get(id=data["order_id"])
        self.assertEqual(order.items.count(), 1)

    # ------------------------------------------------------------------
    # TEST 2 — table reuses existing open order  (unchanged)
    # ------------------------------------------------------------------

    def test_table_reuse_open_order(self):

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            table=self.table, created_by=self.user, status="open",
        )

        response = self.client.post(
            "/create-order/",
            json.dumps({"table_id": self.table.id, "cart": [{"id": self.item.id, "quantity": 1}]}),
            content_type="application/json",
        )

        data = response.json()
        self.assertEqual(order.id, data["order_id"])

    # ------------------------------------------------------------------
    # TEST 3 — send to kitchen
    #
    # Changed: assertion was order.status == "confirmed" which is wrong.
    # send_to_kitchen → create_kot never changes order.status.
    # What actually changes:
    #   - KOTBatch is created with status="confirmed"
    #   - OrderItem.status flips from "pending" → "sent"
    #   - order.status stays "open"
    # ------------------------------------------------------------------

    def test_send_to_kitchen(self):

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            table=self.table, created_by=self.user, status="open",
        )
        order_item = OrderItem.objects.create(
            order=order, menu_item=self.item,
            quantity=1, price=100, gst_percentage=5, total_price=105,
        )

        response = self.client.post(f"/send-to-kitchen/{order.id}/")
        self.assertEqual(response.status_code, 200)

        # Order status is unchanged — send_to_kitchen does not transition it
        order.refresh_from_db()
        self.assertEqual(order.status, "open")

        # The item is now "sent" to the kitchen — that is the real effect
        order_item.refresh_from_db()
        self.assertEqual(order_item.status, "sent")

    # ------------------------------------------------------------------
    # TEST 4 — kitchen marks item as preparing
    #
    # Changed: /update-order-status/ is gone. Status is now per-item.
    # Endpoint: POST /item-start/<item_id>/
    # Requires: item.status == "sent"  (set by create_kot / send_to_kitchen)
    # ------------------------------------------------------------------

    def test_kitchen_preparing(self):

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            table=self.table, created_by=self.user, status="confirmed",
        )
        # After send_to_kitchen, items carry status="sent".
        # We create the item directly in that state to isolate this test.
        order_item = OrderItem.objects.create(
            order=order, menu_item=self.item,
            quantity=1, price=100, gst_percentage=5, total_price=105,
            status="sent",
        )

        response = self.client.post(f"/item-start/{order_item.id}/")

        self.assertEqual(response.status_code, 200)

        order_item.refresh_from_db()
        self.assertEqual(order_item.status, "preparing")

    # ------------------------------------------------------------------
    # TEST 5 — kitchen marks item as ready
    #
    # Changed: /update-order-status/ is gone.
    # Endpoint: POST /item-ready/<item_id>/
    # Requires: item.status == "preparing"
    # ------------------------------------------------------------------

    def test_kitchen_ready(self):

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            table=self.table, created_by=self.user, status="confirmed",
        )
        order_item = OrderItem.objects.create(
            order=order, menu_item=self.item,
            quantity=1, price=100, gst_percentage=5, total_price=105,
            status="preparing",
        )

        response = self.client.post(f"/item-ready/{order_item.id}/")

        self.assertEqual(response.status_code, 200)

        order_item.refresh_from_db()
        self.assertEqual(order_item.status, "ready")

    # ------------------------------------------------------------------
    # TEST 6 — waiter call  (unchanged)
    # ------------------------------------------------------------------

    def test_waiter_call(self):

        WaiterCall.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table
        )
        self.assertEqual(WaiterCall.objects.count(), 1)

    # ------------------------------------------------------------------
    # TEST 7 — mark order paid
    #
    # Changed: /update-order-status/ is gone.
    # Endpoint: POST /pay/<order_id>/  with JSON {"method": "cash", "amount": ...}
    #
    # Required setup that the old test skipped:
    #   1. PaymentConfig  — pay_order enforces outlet-level method config
    #   2. CashSession    — pay_order blocks payment if no open cash session
    #
    # Assertion: after full payment the order transitions to "closed",
    # not "paid" (the view calls order.status = "closed" after full payment).
    # ------------------------------------------------------------------

    def test_payment(self):

        # pay_order checks PaymentConfig to enforce enabled methods
        PaymentConfig.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            cash_enabled=True,
            upi_enabled=True,
            card_enabled=False,
        )

        # pay_order blocks payment when no open CashSession exists
        CashSession.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            opened_by=self.user,
            status="open",
        )

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            table=self.table, created_by=self.user,
            status="open",
            grand_total=Decimal("500.00"),
        )

        response = self.client.post(
            f"/pay/{order.id}/",
            json.dumps({"method": "cash", "amount": "500.00"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()
        # pay_order closes the order after the balance reaches zero
        self.assertEqual(order.status, "closed")
