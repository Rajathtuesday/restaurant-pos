from django.test import TestCase, Client
from accounts.models import User
from tenants.models import Tenant, Outlet
from menu.models import MenuCategory, MenuItem
from orders.models import Table, Order, OrderItem, WaiterCall
import json


class POSTestCase(TestCase):

    def setUp(self):

        self.client = Client()

        # tenant
        self.tenant = Tenant.objects.create(name="Demo Restaurant")

        # outlet
        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Branch"
        )

        # user
        self.user = User.objects.create_user(
            username="owner",
            password="1234",
            tenant=self.tenant,
            outlet=self.outlet,
            role="owner"
        )

        self.client.login(username="owner", password="1234")

        # table
        self.table = Table.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Table 1"
        )

        # menu
        self.category = MenuCategory.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Food"
        )

        self.item = MenuItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Burger",
            price=100
        )

    # -----------------------------
    # TEST 1
    # create order
    # -----------------------------

    def test_create_order(self):

        response = self.client.post(
            "/create-order/",
            json.dumps({
                "table_id": self.table.id,
                "cart": [
                    {"id": self.item.id, "quantity": 2}
                ]
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        data = response.json()

        self.assertTrue(data["success"])

        order = Order.objects.get(id=data["order_id"])

        self.assertEqual(order.items.count(), 1)

    # -----------------------------
    # TEST 2
    # table reuse open order
    # -----------------------------

    def test_table_reuse_open_order(self):

        # create order first
        order = Order.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            table=self.table,
            created_by=self.user,
            status="open"
        )

        response = self.client.post(
            "/create-order/",
            json.dumps({
                "table_id": self.table.id,
                "cart": [
                    {"id": self.item.id, "quantity": 1}
                ]
            }),
            content_type="application/json"
        )

        data = response.json()

        self.assertEqual(order.id, data["order_id"])

    # -----------------------------
    # TEST 3
    # send to kitchen
    # -----------------------------

    def test_send_to_kitchen(self):

        order = Order.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            table=self.table,
            created_by=self.user,
            status="open"
        )

        OrderItem.objects.create(
            order=order,
            menu_item=self.item,
            quantity=1,
            price=100,
            gst_percentage=5,
            total_price=105
        )

        response = self.client.post(f"/send-to-kitchen/{order.id}/")

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()

        self.assertEqual(order.status, "confirmed")

    # -----------------------------
    # TEST 4
    # kitchen preparing
    # -----------------------------

    def test_kitchen_preparing(self):

        order = Order.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            table=self.table,
            created_by=self.user,
            status="confirmed"
        )

        response = self.client.post(
            f"/update-order-status/{order.id}/",
            json.dumps({"status": "preparing"}),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()

        self.assertEqual(order.status, "preparing")

    # -----------------------------
    # TEST 5
    # mark ready
    # -----------------------------

    def test_kitchen_ready(self):

        order = Order.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            table=self.table,
            created_by=self.user,
            status="preparing"
        )

        response = self.client.post(
            f"/update-order-status/{order.id}/",
            json.dumps({"status": "ready"}),
            content_type="application/json"
        )

        order.refresh_from_db()

        self.assertEqual(order.status, "ready")

    # -----------------------------
    # TEST 6
    # waiter call
    # -----------------------------

    def test_waiter_call(self):

        WaiterCall.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            table=self.table
        )

        self.assertEqual(WaiterCall.objects.count(), 1)

    # -----------------------------
    # TEST 7
    # mark order paid
    # -----------------------------

    def test_payment(self):

        order = Order.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            table=self.table,
            created_by=self.user,
            status="ready"
        )

        response = self.client.post(
            f"/update-order-status/{order.id}/",
            json.dumps({"status": "paid"}),
            content_type="application/json"
        )

        order.refresh_from_db()

        self.assertEqual(order.status, "paid")