"""
An order request the server has already taken is never taken twice.

Why: an order placed with no internet is kept on the device and sent
again when the connection is back (static/js/core/offline.js). If the
server took the order but its answer was lost on the way (the connection
dropped in that second), the device could not tell, kept the order, and
sent it again: two orders, or every dish twice on the table's bill. Two
tabs of the same device replaying the same saved order did the same.

The screen now gives every request its own id (request_id) and the
server remembers the ids it has taken (orders.OrderRequest). The same id
again is answered with the first order and changes nothing.

Run: python manage.py test orders.tests.test_order_request_id
"""
import json
from decimal import Decimal as D
from threading import Barrier, Thread

from django.db import connection
from django.test import Client, TestCase, TransactionTestCase

from accounts.models import User
from core.testing import freeze_ratelimit_clock
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, OrderRequest, Table
from tenants.models import Outlet, Tenant
from tokens.models import TokenOrder

FIRST = "0b0e7c1e-7d0a-4f6e-9b1a-3f0c2d9a1e11"
SECOND = "5c2f9a44-1b7d-4a3e-8c55-9e1d7f3b2a22"


def build(test, tenant_type="fine_dining", name="Request Id"):
    test.tenant = Tenant.objects.create(name=name, tenant_type=tenant_type)
    test.outlet = Outlet.objects.create(tenant=test.tenant, name="Main")
    test.cashier = User.objects.create_user(username=f"cashier-{test.tenant.id}", password="pw", role="cashier",
                                            tenant=test.tenant, outlet=test.outlet)
    food = MenuCategory.objects.create(tenant=test.tenant, outlet=test.outlet, name="Food")
    test.dish = MenuItem.objects.create(tenant=test.tenant, outlet=test.outlet, category=food,
                                        name="Samosa", price=D("30.00"), gst_percentage=D("0"))
    test.table = Table.objects.create(tenant=test.tenant, outlet=test.outlet, name="T1")


def post(client, **payload):
    return client.post("/create-order/", data=json.dumps(payload), content_type="application/json")


class OrderRequestIdTest(TestCase):

    def setUp(self):
        freeze_ratelimit_clock(self)
        build(self)
        self.client = Client()
        self.client.force_login(self.cashier)
        self.cart = [{"id": self.dish.id, "quantity": 2}]

    def orders(self):
        return Order.objects.filter(tenant=self.tenant)

    def test_a_walk_in_order_sent_twice_is_one_order(self):
        first = post(self.client, cart=self.cart, source="takeaway", request_id=FIRST)
        again = post(self.client, cart=self.cart, source="takeaway", request_id=FIRST)
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(self.orders().count(), 1)
        self.assertEqual(again.json()["order_id"], first.json()["order_id"])
        self.assertTrue(again.json()["success"])
        self.assertTrue(again.json()["repeat"])
        self.assertNotIn("repeat", first.json())
        self.assertEqual(again.json()["status_token"], first.json()["status_token"])

    def test_dishes_sent_twice_to_a_table_are_on_its_bill_once(self):
        first = post(self.client, cart=self.cart, table_id=self.table.id, source="dine_in", request_id=FIRST)
        total = Order.objects.get(id=first.json()["order_id"]).grand_total
        again = post(self.client, cart=self.cart, table_id=self.table.id, source="dine_in", request_id=FIRST)
        order = Order.objects.get(id=again.json()["order_id"])
        self.assertEqual(self.orders().count(), 1)
        self.assertEqual(sum(i.quantity for i in order.items.all()), 2)
        self.assertEqual(order.grand_total, total)

    def test_a_second_round_for_the_table_has_its_own_id_and_is_added(self):
        post(self.client, cart=self.cart, table_id=self.table.id, source="dine_in", request_id=FIRST)
        post(self.client, cart=self.cart, table_id=self.table.id, source="dine_in", request_id=SECOND)
        order = self.orders().get()
        self.assertEqual(sum(i.quantity for i in order.items.all()), 4)
        self.assertEqual(OrderRequest.objects.for_outlet(self.tenant, self.outlet).count(), 2)

    def test_a_request_with_no_id_works_as_it_always_did(self):
        post(self.client, cart=self.cart, source="takeaway")
        post(self.client, cart=self.cart, source="takeaway")
        self.assertEqual(self.orders().count(), 2)
        self.assertEqual(OrderRequest.objects.for_outlet(self.tenant, self.outlet).count(), 0)

    def test_a_refused_request_does_not_use_its_id_up(self):
        refused = post(self.client, cart=[{"id": 999999, "quantity": 1}], source="takeaway", request_id=FIRST)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.orders().count(), 0, "a refused order left a row behind")
        fixed = post(self.client, cart=self.cart, source="takeaway", request_id=FIRST)
        self.assertEqual(fixed.status_code, 200)
        self.assertNotIn("repeat", fixed.json())
        self.assertEqual(self.orders().count(), 1)

    def test_an_id_that_is_not_an_id_is_refused(self):
        for bad in ("x" * 65, "has spaces", "<script>", 12345, ["a"], {"a": 1}):
            response = post(self.client, cart=self.cart, source="takeaway", request_id=bad)
            self.assertEqual(response.status_code, 400, bad)
            self.assertEqual(response.json()["error"], "Invalid request id.")
        self.assertEqual(self.orders().count(), 0)

    def test_another_restaurant_using_the_same_id_gets_its_own_order(self):
        mine = post(self.client, cart=self.cart, source="takeaway", request_id=FIRST).json()
        other = TestCase()
        build(other, name="Other Place")
        theirs_client = Client()
        theirs_client.force_login(other.cashier)
        theirs = post(theirs_client, cart=[{"id": other.dish.id, "quantity": 1}], source="takeaway",
                      request_id=FIRST).json()
        self.assertTrue(theirs["success"])
        self.assertNotIn("repeat", theirs)
        self.assertNotEqual(theirs["order_id"], mine["order_id"])
        self.assertEqual(Order.objects.get(id=theirs["order_id"]).tenant, other.tenant)

    def test_a_guest_at_the_table_sending_twice_orders_once(self):
        guest = Client()
        token = str(self.table.qr_token)
        first = post(guest, cart=self.cart, table_token=token, source="web", request_id=FIRST)
        again = post(guest, cart=self.cart, table_token=token, source="web", request_id=FIRST)
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertEqual(again.json()["order_id"], first.json()["order_id"])
        self.assertEqual(sum(i.quantity for i in OrderItem.objects.filter(order__tenant=self.tenant)), 2)

    def test_a_guest_at_another_table_is_not_handed_the_first_tables_order(self):
        """A request id is not a key to someone else's order: a guest is only
        ever answered about the table they scanned."""
        staff_order = post(self.client, cart=self.cart, table_id=self.table.id, source="dine_in",
                           request_id=FIRST).json()
        other_table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T2")
        guest = Client()
        response = post(guest, cart=self.cart, table_token=str(other_table.qr_token), source="web", request_id=FIRST)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("order_id", response.json())
        self.assertNotIn("status_token", response.json())
        self.assertNotIn(str(staff_order["order_id"]), response.content.decode())
        self.assertFalse(Order.objects.filter(table=other_table).exists())


class TokenCounterRequestIdTest(TestCase):
    """A cafe gives every order a token number; a repeat must not spend one."""

    def setUp(self):
        freeze_ratelimit_clock(self)
        build(self, tenant_type="cafe", name="Token Cafe")
        self.client = Client()
        self.client.force_login(self.cashier)

    def test_a_repeat_spends_no_token_number(self):
        cart = [{"id": self.dish.id, "quantity": 1}]
        first = post(self.client, cart=cart, source="counter", request_id=FIRST).json()
        again = post(self.client, cart=cart, source="counter", request_id=FIRST).json()
        fresh = post(self.client, cart=cart, source="counter", request_id=SECOND).json()
        self.assertEqual(again["order_id"], first["order_id"])
        numbers = sorted(TokenOrder.objects.filter(tenant=self.tenant).values_list("token_number", flat=True))
        self.assertEqual(numbers, [1, 2], "the repeat took a token number of its own")
        self.assertEqual(TokenOrder.objects.get(order_id=fresh["order_id"]).token_number, 2)


class SameRequestAtTheSameMomentTest(TransactionTestCase):
    """Two tabs of one device replay the same saved order at the same
    moment: both pass the "have I seen this id" look, so the database has
    to be the one that says no. Real threads, real connections."""

    def setUp(self):
        build(self)

    def fire(self, n, payload):
        results = [None] * n
        start = Barrier(n)

        def one(index):
            try:
                client = Client()
                client.force_login(self.cashier)
                start.wait(timeout=10)
                results[index] = post(client, **payload)
            except Exception as e:  # noqa: BLE001 -- recorded for the assertion
                results[index] = f"ERROR: {e}"
            finally:
                connection.close()

        threads = [Thread(target=one, args=(i,)) for i in range(n)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return results

    def check(self, results):
        self.assertEqual([r for r in results if isinstance(r, str)], [])
        self.assertEqual([r.status_code for r in results], [200] * len(results), [r.content for r in results])
        self.assertEqual(len({r.json()["order_id"] for r in results}), 1, "the same request made more than one order")
        self.assertEqual(sum(1 for r in results if not r.json().get("repeat")), 1, "exactly one of them is the original")

    def test_six_at_once_for_a_walk_in_make_one_order(self):
        results = self.fire(6, {"cart": [{"id": self.dish.id, "quantity": 1}], "source": "takeaway", "request_id": FIRST})
        self.check(results)
        self.assertEqual(Order.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(OrderItem.objects.filter(order__tenant=self.tenant).count(), 1)

    def test_six_at_once_for_a_table_put_the_dish_on_its_bill_once(self):
        results = self.fire(6, {"cart": [{"id": self.dish.id, "quantity": 1}], "table_id": self.table.id,
                                "source": "dine_in", "request_id": FIRST})
        self.check(results)
        order = Order.objects.get(tenant=self.tenant)
        self.assertEqual(sum(i.quantity for i in order.items.all()), 1)
