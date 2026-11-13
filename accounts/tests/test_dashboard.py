"""
The owner's dashboard (V6, 13 Nov 2026): today first, tiles grouped by
job, Rasova's own tools for the superuser only.

Why: the page opened on 17 identical tiles with "Today's Performance" at
the bottom, the superuser's tools ("Admin: Django Backend") sat among the
owner's, and every tile was decided by nested template conditions no test
read. The rules are now plain data (accounts/dashboard_tiles.py).

Run: python manage.py test accounts.tests.test_dashboard
"""
from decimal import Decimal
from types import SimpleNamespace

from django.test import Client, TestCase

from accounts.dashboard_tiles import dashboard_tiles, needs_a_look, today_blocks
from core.tests.test_tenant_walk import Restaurant

ALL = {"floor_plan", "token_system", "running_order", "cover_charge", "kitchen_display", "waiter_call",
       "reports", "qr_menu", "inventory", "shift_management", "reservations", "crm"}


def person(role, superuser=False):
    return SimpleNamespace(role=role, is_superuser=superuser)


def labels(groups):
    return {g.key: [t.label for t in g.tiles] for g in groups}


class TilesTest(TestCase):

    def test_an_owner_sees_service_and_back_office_and_none_of_rasovas_tools(self):
        seen = labels(dashboard_tiles(person("owner"), ALL))
        self.assertEqual(list(seen), ["service", "office"])
        self.assertEqual(seen["service"], ["Tables", "Tokens", "Token Board", "Live Orders", "Covers", "Kitchen",
                                           "Billing", "Calls"])
        self.assertEqual(seen["office"][:3], ["Reports", "Cash Register", "History"])
        self.assertEqual(seen["office"][-1], "Setup")

    def test_the_superuser_gets_a_group_of_their_own(self):
        seen = labels(dashboard_tiles(person("owner", superuser=True), ALL))
        self.assertEqual(seen["staff"], ["Partner", "Features", "Admin"])

    def test_a_reseller_sees_partner_and_a_manager_sees_no_staff_tools(self):
        self.assertEqual(labels(dashboard_tiles(person("agent"), ALL))["staff"], ["Partner"])
        self.assertNotIn("staff", labels(dashboard_tiles(person("manager", superuser=True), ALL)))

    def test_a_cashier_sees_service_only(self):
        self.assertEqual(list(labels(dashboard_tiles(person("cashier"), ALL))), ["service"])

    def test_tiles_follow_the_restaurants_features(self):
        seen = labels(dashboard_tiles(person("owner"), {"floor_plan"}))
        self.assertEqual(seen["service"], ["Tables", "Billing"])
        self.assertEqual(seen["office"], ["Cash Register", "History", "Menu", "Setup"])

    def test_a_direct_billing_counter_gets_new_order_as_an_action_with_its_count(self):
        tiles = dashboard_tiles(person("owner"), {"token_system"}, direct_billing_mode=True, active_token_count=3)
        new_order = tiles[0].tiles[0]
        self.assertEqual((new_order.label, new_order.action, new_order.href, new_order.badge),
                         ("New Order", "dashboard.new-order", "", 3))


class TodayTest(TestCase):

    def metric(self, **extra):
        return {"outlet": "Main", "revenue": Decimal("125000.50"), "orders": 40, "avg_order_value": Decimal("3125.0125"),
                "active_tables": 5, "kitchen_orders": 2, "low_stock": 0, "voids_today": 0,
                "discounts_amount": 0, **extra}

    def test_the_numbers_as_words(self):
        [block] = today_blocks([self.metric()])
        self.assertEqual(block, {"outlet": "Main", "revenue": "₹1,25,000.50", "orders": 40, "avg": "₹3,125",
                                 "tables": 5, "kitchen": 2, "alerts": []})

    def test_what_needs_a_look_and_where_it_leads(self):
        alerts = needs_a_look(self.metric(voids_today=1, discounts_amount=Decimal("450"), low_stock=3))
        self.assertEqual([(level, words) for level, words, _ in alerts],
                         [("alert", "1 void today"), ("warn", "₹450 given in discounts"),
                          ("alert", "3 ingredients low on stock")])
        self.assertEqual([href for *_, href in alerts], ["/reports/audit/", "/reports/audit/", "/inventory/board/"])


class DashboardPageTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.r = Restaurant("Alpha", "dash-alpha")

    def page(self, user):
        client = Client()
        client.force_login(user)
        response = client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_today_comes_before_the_tiles(self):
        html = self.page(self.r.owner)
        self.assertLess(html.index('id="today"'), html.index('id="group-service"'))
        self.assertLess(html.index('id="group-service"'), html.index('id="group-office"'))
        self.assertIn("Sales today", html)

    def test_an_owner_never_sees_the_backend(self):
        html = self.page(self.r.owner)
        self.assertNotIn('href="/admin/"', html)
        self.assertNotIn("Rasova staff", html)

    def test_the_minute_refresh_sends_the_same_blocks(self):
        client = Client()
        client.force_login(self.r.owner)
        answer = client.get("/dashboard/metrics.json").json()
        self.assertEqual(answer["today"][0]["outlet"], answer["metrics"][0]["outlet"])
        self.assertTrue(answer["today"][0]["revenue"].startswith("₹"))
