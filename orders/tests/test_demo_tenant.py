"""
The public /live-demo/ magic link lets a prospect skip signup entirely and
land straight on a live-looking floor plan, replacing "here's a video" or
"let's schedule a call" as the reply to an inbound demo request. Backing it
is a dedicated "Demo Bistro" tenant, created and reset by
orders/scripts/demo_seed.py -- idempotent, safe to run on a schedule so a
visitor never inherits a mess left by an earlier one.

Deliberately NOT the same thing as core/views.py's DEBUG-only /demo/
tenant-switcher (an internal dev tool, 404s outside DEBUG) -- this one is a
real production entry point with no auth required at all.

Run: python manage.py test orders.tests.test_demo_tenant
"""
from django.test import TestCase, Client
from django.urls import reverse

from accounts.models import User
from tenants.models import Tenant
from orders.models import Order, Table
from menu.models import MenuItem
from orders.scripts.demo_seed import (
    create_or_reset_demo_tenant, DEMO_TENANT_NAME, DEMO_OWNER_USERNAME,
)


class DemoSeedTests(TestCase):
    """orders/scripts/demo_seed.py -- the setup/reset logic itself."""

    def test_creates_the_demo_tenant_and_owner(self):
        tenant = create_or_reset_demo_tenant()
        self.assertEqual(tenant.name, DEMO_TENANT_NAME)
        self.assertTrue(tenant.slug)
        owner = User.objects.get(username=DEMO_OWNER_USERNAME)
        self.assertEqual(owner.role, "owner")
        self.assertEqual(owner.tenant_id, tenant.id)

    def test_demo_owner_has_no_usable_password(self):
        """Must never be reachable through the normal /login/ form, even by
        someone who guessed the username -- only the magic link can start a
        session for this account."""
        create_or_reset_demo_tenant()
        owner = User.objects.get(username=DEMO_OWNER_USERNAME)
        self.assertFalse(owner.has_usable_password())

    def test_ai_menu_import_is_disabled_for_the_demo_tenant(self):
        """Protects the shared 20-request/day Gemini free tier from being
        burned by a curious demo visitor uploading menu photos."""
        from core.features import has_feature
        tenant = create_or_reset_demo_tenant()
        self.assertFalse(has_feature(tenant, "ai_menu_import"))

    def test_razorpay_is_not_enabled_for_the_demo_tenant(self):
        from core.features import has_feature
        tenant = create_or_reset_demo_tenant()
        self.assertFalse(has_feature(tenant, "razorpay_gateway"))

    def test_seeds_tables_and_a_real_menu(self):
        tenant = create_or_reset_demo_tenant()
        self.assertEqual(Table.objects.filter(tenant=tenant).count(), 8)
        self.assertGreater(MenuItem.objects.filter(tenant=tenant).count(), 10)

    def test_seeds_sample_orders_so_the_floor_plan_is_not_empty(self):
        tenant = create_or_reset_demo_tenant()
        self.assertEqual(Order.objects.filter(tenant=tenant).count(), 2)

    def test_running_twice_is_idempotent_not_duplicated(self):
        create_or_reset_demo_tenant()
        create_or_reset_demo_tenant()
        self.assertEqual(Tenant.objects.filter(name=DEMO_TENANT_NAME).count(), 1)
        tenant = Tenant.objects.get(name=DEMO_TENANT_NAME)
        self.assertEqual(Table.objects.filter(tenant=tenant).count(), 8)
        self.assertEqual(MenuItem.objects.filter(tenant=tenant).count(), 17)

    def test_reset_clears_whatever_a_visitor_left_behind(self):
        """The whole point of running this on a schedule: a visitor's mess
        (extra orders, voided items, whatever) must not survive a reset."""
        tenant = create_or_reset_demo_tenant()
        outlet = tenant.outlets.first()
        table = Table.objects.filter(tenant=tenant).first()
        Order.objects.create(tenant=tenant, outlet=outlet, table=table, status="closed")
        self.assertEqual(Order.objects.filter(tenant=tenant).count(), 3)

        create_or_reset_demo_tenant()
        self.assertEqual(Order.objects.filter(tenant=tenant).count(), 2)

    def test_reset_survives_a_paid_demo_order(self):
        """Real bug, found live: Payment.order is on_delete=PROTECT, so a
        demo visitor who actually completed a payment (exactly what the
        onboarding banner tells them to try -- "bill Table 4") left an
        Order the old reset couldn't delete, breaking every reset after
        it, including the scheduled one every 2 hours."""
        from orders.models import Payment
        tenant = create_or_reset_demo_tenant()
        outlet = tenant.outlets.first()
        table = Table.objects.filter(tenant=tenant).first()
        paid_order = Order.objects.create(
            tenant=tenant, outlet=outlet, table=table, status="closed", grand_total="450.00",
        )
        Payment.objects.create(order=paid_order, method="upi", amount="450.00")

        create_or_reset_demo_tenant()  # must not raise ProtectedError
        self.assertEqual(Order.objects.filter(tenant=tenant).count(), 2)
        self.assertEqual(Payment.objects.filter(order__tenant=tenant).count(), 0)

    def test_upi_id_is_never_set_for_the_demo_tenant(self):
        """A real UPI QR shown to strangers on the open internet would be a
        real payment address collecting real money for nothing -- must
        stay blank."""
        from setup.models import PaymentConfig
        tenant = create_or_reset_demo_tenant()
        outlet = tenant.outlets.first()
        config, _ = PaymentConfig.for_outlet(outlet, tenant)
        self.assertFalse(config.upi_id)


class DemoLoginViewTests(TestCase):
    """The /live-demo/ magic link itself."""

    def setUp(self):
        self.client = Client()

    def test_logs_straight_in_and_redirects_to_the_floor_plan(self):
        create_or_reset_demo_tenant()
        resp = self.client.get(reverse("live-demo"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.endswith("/tables/"))

        # A follow-up request must now be an authenticated session, as the
        # demo owner -- not just a redirect that goes nowhere real.
        resp2 = self.client.get(reverse("dashboard"))
        self.assertNotEqual(resp2.status_code, 302)  # not bounced to /login/

    def test_logged_in_user_is_the_demo_owner(self):
        create_or_reset_demo_tenant()
        self.client.get(reverse("live-demo"))
        user_id = self.client.session.get("_auth_user_id")
        self.assertIsNotNone(user_id)
        self.assertEqual(User.objects.get(id=user_id).username, DEMO_OWNER_USERNAME)

    def test_missing_demo_tenant_fails_gracefully_not_with_a_500(self):
        """If the seed command has never been run yet, a visitor must see a
        clean redirect, not a crash."""
        resp = self.client.get(reverse("live-demo"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(User.objects.filter(username=DEMO_OWNER_USERNAME).count(), 0)

    def test_wrong_password_login_attempts_never_lock_the_demo_owner(self):
        """The whole reason this is a magic link and not a shared typed
        password: AXES_FAILURE_LIMIT (5) would lock a public shared account
        out for real visitors as soon as a handful of strangers mistyped a
        shared password. Confirms repeated failed /login/ attempts against
        the demo username don't block the magic link itself."""
        create_or_reset_demo_tenant()
        for _ in range(6):
            self.client.post(reverse("login"), {
                "username": DEMO_OWNER_USERNAME, "password": "wrong-guess",
            })
        resp = self.client.get(reverse("live-demo"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.endswith("/tables/"))
