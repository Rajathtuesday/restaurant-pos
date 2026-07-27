# menu/tests.py
from django.core.cache import cache
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from decimal import Decimal
import json

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

    def test_update_menu_item_with_unparseable_parcel_charge_logs_a_warning(self):
        # Bad input silently leaves parcel_charge unchanged rather than
        # erroring (intentional best-effort default) - but that used to be
        # completely invisible. Confirms both: existing behavior preserved,
        # and the failure is now traceable in logs.
        original = self.item.parcel_charge
        with self.assertLogs("pos.menu", level="WARNING") as cm:
            response = self.client.post(
                reverse("update_menu_item", args=[self.item.id]),
                data={
                    "name": "Test Dish", "price": "150.00",
                    "category": self.category.id,
                    "parcel_charge": "not-a-number",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.parcel_charge, original)
        self.assertTrue(any("parcel_charge" in msg for msg in cm.output))

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
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=table, status="open",
        )
        for _ in range(30):
            resp = self.client.get(reverse("order_status", args=[order.id]))
            self.assertEqual(resp.status_code, 200)
        resp = self.client.get(reverse("order_status", args=[order.id]))
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
        # 10/min per IP -- 10 different tables should all succeed (the
        # per-table debounce never applies here, they're all distinct
        # tables), the 11th should be rejected by the per-IP limit instead.
        for i in range(10):
            table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name=f"T{i}")
            resp = self.client.post(reverse("call_waiter", args=[table.qr_token]))
            self.assertEqual(resp.status_code, 200)
        extra_table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="Extra")
        resp = self.client.post(reverse("call_waiter", args=[extra_table.qr_token]))
        self.assertEqual(resp.status_code, 429)