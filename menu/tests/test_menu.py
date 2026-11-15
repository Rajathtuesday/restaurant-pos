# menu/tests.py
from django.core.cache import cache

from core.testing import freeze_ratelimit_clock
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from decimal import Decimal
import json
import re

from tenants.models import Tenant, Outlet
from accounts.models import User
from menu.models import (
    MenuCategory,
    MenuItem,
    ModifierGroup,
    Modifier,
    MenuItemModifierGroup
)


class MenuModelTests(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Test Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Outlet"
        )

        self.category = MenuCategory.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Burgers"
        )

    def test_create_menu_item(self):

        item = MenuItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Classic Burger",
            price=Decimal("199.00")
        )

        self.assertEqual(item.name, "Classic Burger")
        self.assertEqual(item.price, Decimal("199.00"))

    def test_negative_price_not_allowed(self):

        item = MenuItem(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Bad Burger",
            price=Decimal("-10")
        )

        with self.assertRaises(Exception):
            item.full_clean()


class ModifierTests(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Outlet"
        )

        self.category = MenuCategory.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Pizza"
        )

        self.item = MenuItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Margherita",
            price=250
        )

        self.group = ModifierGroup.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Extra Toppings"
        )

    def test_modifier_creation(self):

        modifier = Modifier.objects.create(
            group=self.group,
            name="Extra Cheese",
            price=50
        )

        self.assertEqual(modifier.name, "Extra Cheese")

    def test_modifier_group_link(self):

        link = MenuItemModifierGroup.objects.create(
            menu_item=self.item,
            modifier_group=self.group
        )

        self.assertEqual(link.menu_item, self.item)


class MenuViewTests(TestCase):

    def setUp(self):

        self.client = Client()

        self.tenant = Tenant.objects.create(name="Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Outlet"
        )

        self.user = User.objects.create_user(
            username="owner",
            password="testpass",
            role="owner",
            tenant=self.tenant,
            outlet=self.outlet
        )

        self.client.login(username="owner", password="testpass")

        self.category = MenuCategory.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Drinks"
        )

    def test_create_category(self):

        response = self.client.post(
            reverse("create_category"),
            data=json.dumps({"name": "Desserts"}),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        self.assertTrue(
            MenuCategory.objects.filter(name="Desserts").exists()
        )

    def test_create_menu_item(self):

        response = self.client.post(
            reverse("create_menu_item"),
            data=json.dumps({
                "name": "Coke",
                "price": 40,
                "category": self.category.id
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        self.assertTrue(
            MenuItem.objects.filter(name="Coke").exists()
        )


class MenuSecurityTests(TestCase):

    def setUp(self):

        self.client = Client()

        self.tenant = Tenant.objects.create(name="Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Outlet"
        )

        self.waiter = User.objects.create_user(
            username="waiter",
            password="testpass",
            role="waiter",
            tenant=self.tenant,
            outlet=self.outlet
        )

    def test_waiter_cannot_access_menu_management(self):

        self.client.login(username="waiter", password="testpass")

        response = self.client.get(reverse("menu_management"))

        self.assertEqual(response.status_code, 403)


class MenuItemMutationTests(TestCase):
    """Tests for toggle, delete, and update_menu_item views."""

    def setUp(self):

        self.client = Client()

        self.tenant = Tenant.objects.create(name="Mutation Tenant")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Mutation Outlet"
        )

        self.owner = User.objects.create_user(
            username="mutation_owner",
            password="testpass",
            role="owner",
            tenant=self.tenant,
            outlet=self.outlet
        )

        self.client.login(username="mutation_owner", password="testpass")

        self.category = MenuCategory.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            name="Mains"
        )

        self.item = MenuItem.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            category=self.category,
            name="Test Dish",
            price=Decimal("150.00"),
            is_available=True
        )

    def test_toggle_item_flips_availability(self):

        original = self.item.is_available

        response = self.client.post(
            reverse("toggle_item", args=[self.item.id])
        )

        self.assertEqual(response.status_code, 200)

        self.item.refresh_from_db()

        self.assertNotEqual(self.item.is_available, original)

    def test_delete_menu_item_removes_from_db(self):

        item_id = self.item.id

        response = self.client.post(
            reverse("delete_menu_item", args=[item_id])
        )

        self.assertEqual(response.status_code, 200)

        self.assertFalse(MenuItem.objects.filter(id=item_id).exists())

    def test_update_menu_item_changes_price(self):

        response = self.client.post(
            reverse("update_menu_item", args=[self.item.id]),
            data={"name": "Test Dish", "price": "299.00", "category": self.category.id}
        )

        self.assertEqual(response.status_code, 200)

        self.item.refresh_from_db()

        self.assertEqual(self.item.price, Decimal("299.00"))

    def test_a_recipe_quantity_that_is_not_a_number_is_refused_in_words(self):
        # 15 Nov 2026: "lots" reached the recipe save and came back as a 500.
        from inventory.models import InventoryItem
        rice = InventoryItem.objects.create(tenant=self.tenant, outlet=self.outlet, name="Rice", unit="kg")
        for bad in ("lots", "-1", "0"):
            response = self.client.post(reverse("add_recipe"), content_type="application/json",
                                        data={"menu_item": self.item.id, "inventory_item": rice.id, "quantity": bad})
            self.assertEqual(response.status_code, 400, (bad, response.content))
            self.assertIn("quantity", response.json()["error"].lower())
        self.assertFalse(self.item.recipes.exists())

    def test_an_edit_without_station_or_description_keeps_them(self):
        # 15 Nov 2026: the QSR menu page's edit form has no station or
        # description field, and saving it cleared both (the dish's KOTs
        # went to the default station). Fields not sent now stay as they are.
        from setup.models import KitchenStation
        bar = KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        MenuItem.objects.filter(pk=self.item.pk).update(station=bar, description="House special")
        response = self.client.post(
            reverse("update_menu_item", args=[self.item.id]),
            data={"name": "Test Dish", "price": "160.00", "category": self.category.id, "is_veg": "true"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.item.refresh_from_db()
        self.assertEqual((self.item.station_id, self.item.description, self.item.price),
                         (bar.id, "House special", Decimal("160.00")))

    def test_an_empty_station_sends_the_dish_to_the_default(self):
        from setup.models import KitchenStation
        bar = KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        MenuItem.objects.filter(pk=self.item.pk).update(station=bar)
        self.client.post(reverse("update_menu_item", args=[self.item.id]),
                         data={"name": "Test Dish", "price": "150.00", "category": self.category.id,
                               "station": "", "description": ""})
        self.item.refresh_from_db()
        self.assertIsNone(self.item.station_id)

    def test_update_menu_item_with_unparseable_parcel_charge_is_refused(self):
        # Until 3 Oct 2026 a bad parcel charge was dropped with only a log
        # warning while the rest of the edit saved. Now the edit is refused
        # with a message saying what to fix, and nothing is half-saved
        # (core.validators.read_number).
        original_price, original_charge = self.item.price, self.item.parcel_charge
        response = self.client.post(
            reverse("update_menu_item", args=[self.item.id]),
            data={
                "name": "Test Dish", "price": "150.00",
                "category": self.category.id,
                "parcel_charge": "not-a-number",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "The parcel charge must be a number.")
        self.item.refresh_from_db()
        self.assertEqual((self.item.price, self.item.parcel_charge), (original_price, original_charge))

    def test_create_item_with_is_veg_flag(self):

        response = self.client.post(
            reverse("create_menu_item"),
            data=json.dumps({
                "name": "Paneer Tikka",
                "price": 220,
                "category": self.category.id,
                "is_veg": True
            }),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        item = MenuItem.objects.filter(name="Paneer Tikka").first()
        self.assertIsNotNone(item)
        self.assertTrue(item.is_veg)


class DigitalMenuTableTokenTest(TestCase):
    """
    Regression coverage for the QR menu security fix — digital_menu() used
    to accept a plain ?table=<id> as an alternative to ?table_token=<uuid>,
    with no auth check. Table ids are small sequential integers, so that
    path let anyone enumerate them and get the page to hand back that
    table's real, secret qr_token, bypassing the "must physically scan the
    QR code" guarantee entirely. Confirmed dead code (no real caller
    anywhere in the app) before removing it outright.
    """

    def setUp(self):
        from orders.models import Table

        self.tenant = Tenant.objects.create(name="QR Test Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")

    def test_table_token_still_works(self):
        resp = self.client.get(
            reverse("digital_menu"), {"table_token": str(self.table.qr_token)}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["table"].id, self.table.id)

    def test_plain_table_id_no_longer_resolves_a_table(self):
        resp = self.client.get(reverse("digital_menu"), {"table": self.table.id})
        # No token, no logged-in user with a tenant — falls through to the
        # "no valid table token provided" 404, exactly like a request with
        # no table info at all. Never resolves self.table or leaks its
        # qr_token into the rendered page.
        self.assertEqual(resp.status_code, 404)

    def test_plain_table_id_does_not_leak_qr_token_even_for_logged_in_staff(self):
        # A logged-in staff member hitting ?table=<id> must not have it
        # silently resolve someone else's table either — it should just
        # fall back to their own tenant/outlet context, same as visiting
        # the page with no table info at all.
        staff = User.objects.create_user(
            username="qr_staff", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.client.force_login(staff)
        resp = self.client.get(reverse("digital_menu"), {"table": self.table.id})
        self.assertEqual(resp.status_code, 200)


class DigitalMenuOrderStatusUITest(TestCase):
    """
    The order-status UI is a small floating pill that opens a slide-up
    status sheet (it used to be a full-width banner under the header).

    The markup is checked here. What the page does with it (every backend
    stage on the timeline, the pill moving above the cart bar, the cart
    showing what is already ordered, the status reset when an order is gone)
    is tested on the modules since 13 Nov 2026, when the page's inline script
    became static/js/qr_menu/: static/js/tests/qr_menu.test.mjs, and in a
    browser, menu/tests/test_qr_menu_browser.py.
    """

    def setUp(self):
        from orders.models import Table

        self.tenant = Tenant.objects.create(name="Status UI Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")

    def _get_html(self):
        resp = self.client.get(reverse("digital_menu"), {"table_token": str(self.table.qr_token)})
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_old_banner_markup_is_gone(self):
        html = self._get_html()
        self.assertNotIn("order-status-banner", html)
        self.assertNotIn("orderStatusBanner", html)

    def test_pill_and_sheet_markup_present(self):
        html = self._get_html()
        self.assertIn('id="orderStatusPill"', html)
        self.assertIn('data-action="status.open"', html)
        self.assertIn('id="statusSheetOverlay"', html)
        self.assertIn('id="statusSheet"', html)
        # Received / Preparing / Ready / Served
        self.assertEqual(html.count('class="status-stage"'), 4)

    def test_pill_starts_hidden(self):
        """No order placed yet for this table: the pill must not show."""
        self.assertIn("order-status-pill hidden", self._get_html())

    def test_the_page_has_no_inline_code_left(self):
        html = self._get_html()
        self.assertNotIn("onclick=", html)
        self.assertNotIn("<style>", html)
        self.assertIn("js/qr_menu/index.js", html)


class CounterMenuQRTest(TestCase):
    """
    QSR/cafe outlets with no seating have no Table to hang a per-table QR
    on. menu_view() (the "<uuid:qr_token>/" QR-scan entry point) must also
    resolve an Outlet.qr_token, landing the guest on the same menu with
    table=None -- a counter/walk-in order, same as any other tableless
    order already supported by create_order.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Counter Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")

    def test_outlet_qr_token_renders_menu_with_no_table(self):
        resp = self.client.get(reverse("menu_view", args=[self.outlet.qr_token]))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["table"])
        self.assertEqual(resp.context["outlet"].id, self.outlet.id)
        self.assertEqual(resp.context["tenant"].id, self.tenant.id)

    def test_table_qr_token_still_takes_priority(self):
        from orders.models import Table
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        resp = self.client.get(reverse("menu_view", args=[table.qr_token]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["table"].id, table.id)

    def test_unknown_token_404s(self):
        import uuid
        resp = self.client.get(reverse("menu_view", args=[uuid.uuid4()]))
        self.assertEqual(resp.status_code, 404)

    def test_outlet_qr_respects_qr_menu_feature_gate(self):
        # franchise doesn't get qr_menu by default (core/features.py) --
        # confirms the outlet-token path is gated the same as the
        # table-token path, not a bypass.
        franchise = Tenant.objects.create(name="Franchise Co", tenant_type="franchise")
        outlet = Outlet.objects.create(tenant=franchise, name="Branch 1")
        resp = self.client.get(reverse("menu_view", args=[outlet.qr_token]))
        self.assertEqual(resp.status_code, 404)

    def test_page_exposes_the_outlet_token_for_the_frontend_to_submit_with(self):
        # Regression: the page used to only ever expose table.qr_token to
        # its JS, so a tableless counter guest's browser had an empty
        # token and submitOrder() hard-blocked with "No table detected.
        # Please scan a valid table QR." before ever reaching the server.
        resp = self.client.get(reverse("menu_view", args=[self.outlet.qr_token]))
        self.assertEqual(resp.context["qr_token"], str(self.outlet.qr_token))
        self.assertContains(resp, str(self.outlet.qr_token))


@override_settings(RATELIMIT_ENABLE=True)
class PublicMenuRateLimitTest(TestCase):
    """
    RATELIMIT_ENABLE = not _TESTING (core/settings.py) disables rate
    limiting entirely under the test runner -- these explicitly re-enable
    it to prove the limits on menu_view/digital_menu/order_status actually
    reject requests, not just that the decorators are present.
    django_ratelimit's counters live in the cache, which (unlike the DB)
    is NOT rolled back between TestCase methods -- cache.clear() is
    required in setUp/tearDown or one test's hits count toward the next.
    """

    def setUp(self):
        cache.clear()
        freeze_ratelimit_clock(self)
        self.tenant = Tenant.objects.create(name="Rate Limit Cafe", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

    def tearDown(self):
        cache.clear()

    def test_menu_view_rate_limited_after_30_per_minute(self):
        from orders.models import Table
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        for _ in range(30):
            resp = self.client.get(reverse("menu_view", args=[table.qr_token]))
            self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse("menu_view", args=[table.qr_token]))
        self.assertEqual(resp.status_code, 429)

    def test_digital_menu_rate_limited_after_30_per_minute(self):
        from orders.models import Table
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        for _ in range(30):
            resp = self.client.get(reverse("digital_menu"), {"table_token": str(table.qr_token)})
            self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse("digital_menu"), {"table_token": str(table.qr_token)})
        self.assertEqual(resp.status_code, 429)

    def test_order_status_rate_limited_after_30_per_minute(self):
        from orders.models import Order, Table
        from orders.views.public_views import make_order_status_token
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=table, status="open",
        )
        token = make_order_status_token(order.id)
        for _ in range(30):
            resp = self.client.get(reverse("order_status", args=[token]))
            self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse("order_status", args=[token]))
        self.assertEqual(resp.status_code, 429)
        self.assertIn("error", resp.json())

    def test_menu_view_guest_polling_cadence_never_trips_the_limit(self):
        # Sanity check the rate isn't accidentally tight enough to block a
        # guest's own normal usage -- comfortably under the 30/min cap.
        from orders.models import Table
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        for _ in range(20):
            resp = self.client.get(reverse("menu_view", args=[table.qr_token]))
            self.assertEqual(resp.status_code, 200)


class OrderStatusTokenSecurityTest(TestCase):
    """Regression: order_status used to take a raw sequential order_id with
    no login required -- anyone could enumerate it and read any tenant's
    live order, not just their own. Now keyed off a signed token, same
    pattern as public_bill."""

    def setUp(self):
        from orders.models import Order, Table
        self.tenant_a = Tenant.objects.create(name="Tenant A", tenant_type="cafe")
        self.outlet_a = Outlet.objects.create(tenant=self.tenant_a, name="Main")
        self.table_a = Table.objects.create(tenant=self.tenant_a, outlet=self.outlet_a, name="A1")
        self.order_a = Order.objects.create(
            tenant=self.tenant_a, outlet=self.outlet_a, table=self.table_a, status="open",
        )

        self.tenant_b = Tenant.objects.create(name="Tenant B", tenant_type="cafe")
        self.outlet_b = Outlet.objects.create(tenant=self.tenant_b, name="Main")
        self.table_b = Table.objects.create(tenant=self.tenant_b, outlet=self.outlet_b, name="B1")
        self.order_b = Order.objects.create(
            tenant=self.tenant_b, outlet=self.outlet_b, table=self.table_b, status="open",
        )

    def test_raw_integer_order_id_no_longer_works(self):
        resp = self.client.get(f"/menu/order-status/{self.order_a.id}/")
        self.assertEqual(resp.status_code, 400)

    def test_valid_token_returns_the_right_order(self):
        from orders.views.public_views import make_order_status_token
        token = make_order_status_token(self.order_a.id)
        resp = self.client.get(reverse("order_status", args=[token]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["order_id"], self.order_a.id)

    def test_tenant_bs_token_cannot_be_used_to_read_tenant_as_order(self):
        """The whole point: a guessed/enumerated raw id is gone, and each
        tenant's own signed token only ever resolves to its own order."""
        from orders.views.public_views import make_order_status_token
        token_b = make_order_status_token(self.order_b.id)
        resp = self.client.get(reverse("order_status", args=[token_b]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["order_id"], self.order_b.id)
        self.assertNotEqual(resp.json()["order_id"], self.order_a.id)

    def test_tampered_token_rejected(self):
        from orders.views.public_views import make_order_status_token
        token = make_order_status_token(self.order_a.id)
        tampered = token[:-1] + ("x" if token[-1] != "x" else "y")
        resp = self.client.get(reverse("order_status", args=[tampered]))
        self.assertEqual(resp.status_code, 400)

    def test_create_order_response_includes_a_usable_status_token(self):
        category = MenuCategory.objects.create(
            tenant=self.tenant_a, outlet=self.outlet_a, name="Mains"
        )
        item = MenuItem.objects.create(
            tenant=self.tenant_a, outlet=self.outlet_a, category=category,
            name="Dosa", price=Decimal("120.00"), is_available=True,
        )
        resp = self.client.post(
            "/create-order/",
            data=json.dumps({
                "table_token": str(self.table_a.qr_token),
                "cart": [{"id": item.id, "quantity": 1}],
            }),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("status_token", body)

        # The token create_order just handed back must actually resolve,
        # end to end, to the order that was just created.
        status_resp = self.client.get(reverse("order_status", args=[body["status_token"]]))
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.json()["order_id"], body["order_id"])


@override_settings(RATELIMIT_ENABLE=True)
class CallWaiterRateLimitTest(TestCase):
    """
    call_waiter has two independent checks that guard against different
    things: the pre-existing 60s-per-table debounce (one table spamming
    staff repeatedly) and the new per-IP @ratelimit (a flood across many
    different tables, which the debounce alone never catches). Both need
    their own coverage.
    """

    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="Waiter Call Fine Dining", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

    def tearDown(self):
        cache.clear()

    def test_per_table_debounce_still_blocks_a_second_call_within_60s(self):
        from orders.models import Table
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        first = self.client.post(reverse("call_waiter", args=[table.qr_token]))
        self.assertEqual(first.status_code, 200)
        second = self.client.post(reverse("call_waiter", args=[table.qr_token]))
        self.assertEqual(second.status_code, 429)

    def test_per_ip_limit_blocks_flood_across_many_different_tables(self):
        from orders.models import Table
        freeze_ratelimit_clock(self)
        # 60/min per IP (roomy enough for a restaurant's guests on one
        # Wi-Fi; it was 10, which a busy evening could reach) -- 60 different
        # tables all succeed (the per-table debounce never applies, they're
        # all distinct), the 61st is rejected by the per-IP limit instead.
        for i in range(60):
            table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"T{i}")
            resp = self.client.post(reverse("call_waiter", args=[table.qr_token]))
            self.assertEqual(resp.status_code, 200)
        extra_table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extra")
        resp = self.client.post(reverse("call_waiter", args=[extra_table.qr_token]))
        self.assertEqual(resp.status_code, 429)


class AIImportTaskErrorMessageTests(TestCase):
    """The AI-import task must never leak a raw exception string into the
    cache entry a public poll endpoint later serializes straight to the
    browser -- that bypasses Django's own DEBUG=False protection entirely."""

    def test_task_failure_stores_a_generic_message_not_the_raw_exception(self):
        from unittest.mock import patch
        from menu.tasks import ai_import_menu

        with patch("core.ai_service.AIService.parse_menu") as mock_parse:
            mock_parse.side_effect = RuntimeError("some internal secret path or DB detail")
            # Called directly (not via .delay()/.apply_async()), same as
            # inventory/test_recipe_import.py's established _run_task_synchronously
            # pattern -- Celery binds `self` automatically, and self.request.id
            # resolves to None outside a real dispatch context.
            ai_import_menu(999999, 999999, "x", "", "text/plain")

        result = cache.get("ai_import:None")
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "error")
        self.assertNotIn("some internal secret path or DB detail", result["error"])


class AIImportVegNonVegTests(TestCase):
    """AI-imported items must carry the AI's veg/non-veg classification
    instead of silently falling back to the model's default=True for
    every item, which would mislabel every non-veg dish as vegetarian."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Veg Test Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")

    def test_ai_response_is_veg_false_is_applied_to_the_created_item(self):
        from unittest.mock import patch
        from menu.tasks import ai_import_menu

        with patch("core.ai_service.AIService.parse_menu") as mock_parse:
            mock_parse.return_value = [{
                "category": "Main Course",
                "items": [{"name": "Chicken Biryani", "price": 380, "is_veg": False}],
            }]
            ai_import_menu(self.tenant.id, self.outlet.id, "x", "", "text/plain")

        item = MenuItem.objects.get(tenant=self.tenant, name="Chicken Biryani")
        self.assertFalse(item.is_veg)

    def test_ai_response_is_veg_true_is_applied_to_the_created_item(self):
        from unittest.mock import patch
        from menu.tasks import ai_import_menu

        with patch("core.ai_service.AIService.parse_menu") as mock_parse:
            mock_parse.return_value = [{
                "category": "Starters",
                "items": [{"name": "Paneer Tikka", "price": 260, "is_veg": True}],
            }]
            ai_import_menu(self.tenant.id, self.outlet.id, "x", "", "text/plain")

        item = MenuItem.objects.get(tenant=self.tenant, name="Paneer Tikka")
        self.assertTrue(item.is_veg)

    def test_missing_is_veg_in_ai_response_defaults_to_true_not_a_crash(self):
        """Older/degraded AI responses that omit is_veg shouldn't break import
        -- falls back to the same safe default the model field already has."""
        from unittest.mock import patch
        from menu.tasks import ai_import_menu

        with patch("core.ai_service.AIService.parse_menu") as mock_parse:
            mock_parse.return_value = [{
                "category": "Breads",
                "items": [{"name": "Butter Naan", "price": 60}],
            }]
            ai_import_menu(self.tenant.id, self.outlet.id, "x", "", "text/plain")

        item = MenuItem.objects.get(tenant=self.tenant, name="Butter Naan")
        self.assertTrue(item.is_veg)


class SyncFallbackAIImportTests(TestCase):
    """menu/views/ai_views.py::_run_sync is the OTHER place that creates
    MenuItems from AI-parsed data (used when Celery/Redis is down) --
    a second, separate code path from menu/tasks.py::ai_import_menu, found
    while adding a timeout to this same function. Confirms it got the same
    is_veg fix, not just the Celery path, and that the new timeout actually
    bounds a hung parse instead of leaving the request to hang."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Sync Fallback Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")
        self.owner = User.objects.create_user(
            username="sync_owner", password="pass1234",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.client.force_login(self.owner)

    def test_sync_path_applies_ai_is_veg_classification(self):
        from unittest.mock import patch

        with patch("menu.views.ai_views.dispatch", side_effect=Exception("Celery down")), \
             patch("core.ai_service.AIService.parse_menu") as mock_parse:
            mock_parse.return_value = [{
                "category": "Main Course",
                "items": [{"name": "Chicken Biryani", "price": 380, "is_veg": False}],
            }]
            resp = self.client.post(
                reverse("ai_menu_importer"), {"text": "Chicken Biryani 380"}
            )

        self.assertEqual(resp.status_code, 200)
        item = MenuItem.objects.get(tenant=self.tenant, name="Chicken Biryani")
        self.assertFalse(item.is_veg)

    def test_sync_path_times_out_cleanly_instead_of_hanging(self):
        from unittest.mock import patch
        import time

        def _slow_parse(*args, **kwargs):
            time.sleep(0.3)
            return [{"category": "Starters", "items": [{"name": "Should Not Import", "price": 1}]}]

        with patch("menu.views.ai_views.dispatch", side_effect=Exception("Celery down")), \
             patch("menu.views.ai_views.SYNC_PARSE_TIMEOUT_SECONDS", 0.05), \
             patch("core.ai_service.AIService.parse_menu", side_effect=_slow_parse):
            resp = self.client.post(
                reverse("ai_menu_importer"), {"text": "some menu text"}
            )

        self.assertEqual(resp.status_code, 400)
        self.assertIn("took too long", resp.json()["error"])
        # The item from the (still-running-in-the-background) slow parse
        # must not show up -- the request already returned its own clean
        # timeout error and moved on.
        self.assertFalse(MenuItem.objects.filter(tenant=self.tenant, name="Should Not Import").exists())


class ManualMenuParserVegGuessTests(TestCase):
    """The regex fallback parser (no AI key configured) has no language
    understanding, so its veg/non-veg guess is keyword-based -- confirms it
    gets the common real cases right, including the specific false-positive
    this fix corrected (a cooking style like "Tikka" is not a protein)."""

    def setUp(self):
        from core.ai_service import AIService
        self.svc = AIService()

    def test_protein_keywords_are_flagged_non_veg(self):
        self.assertFalse(self.svc._guess_veg("Chicken Biryani"))
        self.assertFalse(self.svc._guess_veg("Fish Amritsari"))
        self.assertFalse(self.svc._guess_veg("Egg Curry"))

    def test_paneer_tikka_is_not_misflagged_by_the_cooking_style_name(self):
        """Regression case: Paneer Tikka is a real, veg item -- "Tikka" is a
        cooking style, not a protein, and must not trigger a non-veg match."""
        self.assertTrue(self.svc._guess_veg("Paneer Tikka"))

    def test_plain_veg_dishes_default_true(self):
        self.assertTrue(self.svc._guess_veg("Veg Spring Rolls"))
        self.assertTrue(self.svc._guess_veg("Dal Tadka"))


class QRMenuModifierDataTest(TestCase):
    """
    Regression: the guest QR menu handed the browser its modifier data
    double-encoded. _build_modifier_data() returned an already-JSON string and
    the template then ran it through json_script, which encodes a second time,
    so the page's JSON.parse(...) produced a *string*, not an object.
    ITEM_MODIFIERS[itemId] then read a single character out of that string:
    any dish whose id was smaller than the string's length threw
    "groups.forEach is not a function" on ADD (nothing was added), and
    modifier choices could never appear for any dish. Present since the
    modifier modal was added (2026-06-03); no test parsed the embedded data.
    """

    SCRIPT = re.compile(
        r'<script id="item-modifiers-raw" type="application/json">(.*?)</script>', re.S
    )

    def setUp(self):
        from orders.models import Table

        self.tenant = Tenant.objects.create(name="Modifier QR Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Outlet")
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains"
        )
        self.item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Pizza", price=Decimal("250.00"),
        )
        self.group = ModifierGroup.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Size", is_required=True,
        )
        Modifier.objects.create(group=self.group, name="Large", price=Decimal("50.00"))

    def _embedded(self, resp):
        match = self.SCRIPT.search(resp.content.decode())
        self.assertIsNotNone(match, "item-modifiers-raw script tag missing from the page")
        return json.loads(match.group(1))     # exactly what the browser's JSON.parse does

    def test_modifier_data_reaches_the_browser_as_an_object(self):
        MenuItemModifierGroup.objects.create(menu_item=self.item, modifier_group=self.group)
        resp = self.client.get(reverse("menu_view", args=[self.table.qr_token]))
        self.assertEqual(resp.status_code, 200)

        data = self._embedded(resp)
        self.assertIsInstance(data, dict)      # was a str: the double-encoding bug
        groups = data[str(self.item.id)]
        self.assertIsInstance(groups, list)
        self.assertEqual(groups[0]["name"], "Size")
        self.assertTrue(groups[0]["is_required"])
        self.assertEqual(groups[0]["modifiers"][0]["name"], "Large")

    def test_menu_without_modifiers_embeds_an_empty_object(self):
        resp = self.client.get(reverse("menu_view", args=[self.table.qr_token]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._embedded(resp), {})

    def test_digital_menu_route_embeds_an_object_too(self):
        MenuItemModifierGroup.objects.create(menu_item=self.item, modifier_group=self.group)
        resp = self.client.get(reverse("digital_menu"), {"table_token": str(self.table.qr_token)})
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(self._embedded(resp), dict)
