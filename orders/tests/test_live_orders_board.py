"""
Live Orders board (orders/views/live_orders.py).

What it must guarantee: only this outlet's open orders, never another
tenant's or another branch's; voided lines hidden; the right edit flags per
role; the feature/role gates; and a query count that stays flat as the
number of open orders grows, because every staff screen polls it.

Run: python manage.py test orders.tests.test_live_orders_board
"""
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, OrderItemModifier, Table
from tenants.models import Outlet, Tenant, TenantFeatureOverride
from tokens.models import TokenOrder


class _Board(TestCase):
    tenant_type = "fine_dining"

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Board Tenant", tenant_type=self.tenant_type)
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.users = {
            role: User.objects.create_user(
                username=f"board_{role}", password="pw", role=role,
                tenant=self.tenant, outlet=self.outlet,
            )
            for role in ("owner", "manager", "cashier", "captain", "waiter", "chef", "kitchen")
        }
        self.cat = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.naan = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.cat, name="Butter Naan", price=Decimal("60"),
        )
        self._tables = 0

    def _order(self, *lines, status="open", table=True, outlet=None, tenant=None, source="dine_in"):
        tenant = tenant or self.tenant
        outlet = outlet or self.outlet
        t = None
        if table:
            self._tables += 1
            t = Table.objects.create(tenant=tenant, outlet=outlet, name=f"T{self._tables}")
        order = Order.objects.create(
            tenant=tenant, outlet=outlet, table=t, status=status, source=source,
            created_by=self.users["cashier"] if tenant == self.tenant else None,
        )
        for qty, st in lines or [(1, "sent")]:
            OrderItem.objects.create(
                order=order, menu_item=self.naan, quantity=qty, price=Decimal("60"),
                gst_percentage=Decimal("0"), total_price=Decimal("60") * qty, status=st,
            )
        order.recalculate_totals()
        return order

    def _get(self, role="cashier", url_name="live-orders-data", user=None):
        client = Client()
        client.force_login(user or self.users[role])
        return client.get(reverse(url_name))


class LiveOrdersScopeTests(_Board):

    def test_page_renders_for_front_of_house(self):
        for role in ("owner", "manager", "cashier", "captain", "waiter"):
            with self.subTest(role=role):
                resp = self._get(role, "live-orders")
                self.assertEqual(resp.status_code, 200)
                self.assertContains(resp, "Live Orders")
                self.assertContains(resp, "js/order_edit.js")

    def test_kitchen_roles_are_turned_away(self):
        for role in ("chef", "kitchen"):
            with self.subTest(role=role):
                self.assertEqual(self._get(role, "live-orders").status_code, 403)
                self.assertEqual(self._get(role).status_code, 403)

    def test_not_logged_in_goes_to_login(self):
        self.assertEqual(Client().get(reverse("live-orders-data")).status_code, 302)

    def test_only_open_orders_of_this_outlet(self):
        open_order = self._order()
        billing = self._order(status="billing")
        self._order(status="paid")
        self._order(status="closed")
        self._order(status="cancelled")
        branch = Outlet.objects.create(tenant=self.tenant, name="Branch 2")
        self._order(outlet=branch)

        data = self._get().json()

        self.assertEqual(sorted(o["id"] for o in data["orders"]), sorted([open_order.id, billing.id]))
        self.assertEqual(data["summary"]["open_orders"], 2)
        self.assertEqual(data["summary"]["awaiting_bill"], 1)

    def test_never_shows_another_tenant(self):
        other_tenant = Tenant.objects.create(name="Other Tenant")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Main")
        other_user = User.objects.create_user(
            username="other_cashier", password="pw", role="cashier", tenant=other_tenant, outlet=other_outlet,
        )
        mine = self._order()
        theirs = self._order(tenant=other_tenant, outlet=other_outlet)

        my_ids = [o["id"] for o in self._get().json()["orders"]]
        their_ids = [o["id"] for o in self._get(user=other_user).json()["orders"]]

        self.assertEqual(my_ids, [mine.id])
        self.assertEqual(their_ids, [theirs.id])

    def test_voided_lines_are_hidden_modifiers_and_notes_shown(self):
        order = self._order((2, "sent"), (1, "voided"))
        line = order.items.get(status="sent")
        line.notes = "Less butter"
        line.save(update_fields=["notes"])
        OrderItemModifier.objects.create(order_item=line, name="Extra Garlic", price=Decimal("10"))

        items = self._get().json()["orders"][0]["items"]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"], 2)
        self.assertEqual(items[0]["modifiers"], ["Extra Garlic"])
        self.assertEqual(items[0]["notes"], "Less butter")
        self.assertTrue(items[0]["in_kitchen"])

    def test_counts_for_the_summary(self):
        self._order((1, "sent"), (1, "preparing"), (1, "ready"))
        self._order((1, "review"), (1, "pending"))

        s = self._get().json()["summary"]

        self.assertEqual((s["in_kitchen"], s["ready"], s["review"]), (2, 1, 1))

    def test_links_point_at_real_screens(self):
        order = self._order()
        o = self._get().json()["orders"][0]
        self.assertEqual(o["add_url"], f"{reverse('billing-view')}?table={order.table_id}")
        self.assertEqual(o["bill_url"], reverse("bill-view", args=[order.id]))
        self.assertEqual(o["detail_url"], reverse("running-order", args=[order.id]))
        self.assertEqual(o["label"], order.table.name)
        self.assertEqual(o["kind"], "table")


class LiveOrdersPermissionFlagTests(_Board):

    def _flags(self, role):
        item = self._get(role).json()["orders"][0]["items"][0]
        return item["can_reduce"], item["needs_manager"]

    def test_served_dish_is_locked_for_cashier_open_for_manager(self):
        self._order((1, "served"))
        self.assertEqual(self._flags("cashier"), (False, True))
        self.assertEqual(self._flags("captain"), (False, True))
        self.assertEqual(self._flags("manager"), (True, False))
        self.assertEqual(self._flags("owner"), (True, False))

    def test_unserved_dish_is_editable_by_front_desk(self):
        self._order((1, "sent"))
        for role in ("cashier", "captain", "manager", "owner"):
            with self.subTest(role=role):
                self.assertEqual(self._flags(role), (True, False))

    def test_waiter_can_look_but_not_edit(self):
        self._order((1, "sent"), (1, "served"))
        data = self._get("waiter").json()
        self.assertFalse(data["can_edit"])
        for item in data["orders"][0]["items"]:
            self.assertEqual((item["can_reduce"], item["needs_manager"]), (False, False))


class LiveOrdersFeatureGateTests(_Board):

    def test_fine_dining_without_running_order_is_blocked(self):
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="running_order", enabled=False)
        self.assertEqual(self._get(url_name="live-orders").status_code, 403)
        self.assertEqual(self._get().status_code, 403)


class LiveOrdersCafeTests(_Board):
    tenant_type = "cafe"

    def _token(self, order, number, online=False):
        return TokenOrder.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            token_number=number, date=date.today(), is_online=online,
        )

    def test_cafe_tenant_gets_the_board_with_token_labels(self):
        counter = self._order(table=False, source="counter")
        self._token(counter, 12)
        online = self._order(table=False, source="zomato")
        self._token(online, 3, online=True)

        orders = {o["id"]: o for o in self._get().json()["orders"]}

        self.assertEqual(orders[counter.id]["label"], "Token #12")
        self.assertEqual(orders[counter.id]["kind"], "token")
        self.assertEqual(orders[counter.id]["add_url"], reverse("token-bill", args=[counter.id]))
        self.assertIsNone(orders[counter.id]["detail_url"])  # cafes don't have the running-order page
        self.assertEqual(orders[online.id]["label"], "Token O-3")
        self.assertEqual(orders[online.id]["kind"], "online")
        self.assertIsNone(orders[online.id]["add_url"])


class LiveOrdersQueryCountTests(_Board):
    """
    The board is polled every 8 s by every open staff screen on a small
    server. Its cost must not grow with the number of open orders, items,
    modifiers or tokens: same number of queries for 1 order as for 10.
    """
    tenant_type = "cafe"  # has tables AND tokens available via overrides below

    def setUp(self):
        super().setUp()
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="running_order", enabled=True)

    def _busy_order(self, n):
        order = self._order((2, "sent"), (1, "ready"), (1, "served"), (1, "voided"), table=(n % 2 == 0))
        for line in order.items.exclude(status="voided"):
            OrderItemModifier.objects.create(order_item=line, name="Extra", price=Decimal("5"))
            OrderItemModifier.objects.create(order_item=line, name="Spicy", price=Decimal("0"))
        if not order.table_id:
            TokenOrder.objects.create(
                tenant=self.tenant, outlet=self.outlet, order=order,
                token_number=n + 1, date=date.today(), is_online=False,
            )
        return order

    def _count(self):
        client = Client()
        client.force_login(self.users["cashier"])
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(reverse("live-orders-data"))
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries), resp.json()

    def test_query_count_is_flat_as_orders_grow(self):
        self._busy_order(0)
        small, small_data = self._count()
        self.assertEqual(len(small_data["orders"]), 1)

        for n in range(1, 10):
            self._busy_order(n)
        large, large_data = self._count()
        self.assertEqual(len(large_data["orders"]), 10)

        self.assertEqual(small, large, "Query count grew with the number of open orders: N+1 on the board")

    def test_query_count_is_small(self):
        for n in range(5):
            self._busy_order(n)
        queries, _ = self._count()
        # Measured: 8 (session, user, feature overrides, the orders query with
        # its joins, and the item / menu item / modifier prefetches). A rise
        # here means a new per-poll query was added; check it is really needed.
        self.assertLessEqual(queries, 8, f"{queries} queries for one poll")
