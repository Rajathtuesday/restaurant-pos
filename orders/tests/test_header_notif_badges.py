# orders/tests/test_header_notif_badges.py
"""
billing.html and tables.html each hand-copied their own header notification
badges independently of core/base.html's defaults -- both had only the
"Calls" (waiter/kitchen) badge, missing the QR-orders badge entirely, and
neither could have the low-stock/system badge since it didn't exist until
it was added centrally. Consolidated into two shared partials
(core/_notif_badges.html for icon-only headers, core/_notif_badges_luxury.html
for the POS screens' text+icon style) so all three badges stay in sync
across every page from one place instead of N hand-copies.

Run: python manage.py test orders.tests.test_header_notif_badges
"""
from django.test import TestCase, Client
from django.urls import reverse
from tenants.models import Tenant, Outlet
from accounts.models import User
from orders.models import Order


class HeaderNotifBadgeConsistencyTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Badge Tenant", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(
            username="badge_mgr", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.waiter = User.objects.create_user(
            username="badge_waiter", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.client = Client()

    def _login(self, user):
        self.client.login(username=user.username, password="pw")

    def test_manager_sees_system_alerts_badge_on_billing(self):
        self._login(self.manager)
        resp = self.client.get("/billing/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="notif-badge-system"', resp.content.decode())

    def test_manager_sees_system_alerts_badge_on_tables(self):
        self._login(self.manager)
        resp = self.client.get("/tables/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="notif-badge-system"', resp.content.decode())

    def test_waiter_does_not_see_system_alerts_badge_on_billing(self):
        self._login(self.waiter)
        resp = self.client.get("/billing/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('id="notif-badge-system"', resp.content.decode())

    def test_billing_now_shows_qr_orders_badge_too(self):
        """
        The badge billing.html was missing entirely before consolidation,
        not just the new one -- fine_dining has both qr_menu and
        floor_plan, so this should now render for a non-chef role.
        """
        self._login(self.manager)
        resp = self.client.get("/billing/")
        self.assertIn('id="notif-badge-qr"', resp.content.decode())

    def test_waiter_calls_badge_still_present_on_billing(self):
        """Regression guard -- the one badge that DID already work must
        not have been lost in the consolidation."""
        self._login(self.manager)
        resp = self.client.get("/billing/")
        content = resp.content.decode()
        self.assertIn('id="notif-badge-waiter"', content)
        self.assertIn('id="notif-badge-waiter-mobile"', content)

    def test_tables_no_longer_ships_its_own_redundant_poller(self):
        """
        The page-local 10s pollNotifications() duplicated the global 8s
        poller in base.html -- removed, not left running alongside it.
        Both happened to share the name "pollNotifications" (base.html's
        global one is scoped inside its own IIFE, so there was never an
        actual collision, just a coincidence) -- the 10000ms interval is
        what's actually unique to the deleted local one.
        """
        self._login(self.manager)
        resp = self.client.get("/tables/")
        self.assertNotIn("setInterval(pollNotifications, 10000)", resp.content.decode())


class TokenBillingHeaderBadgeTest(TestCase):
    """
    Cafe/QSR tenants don't have waiter_call or qr_menu in their default
    feature set (no waiter-run floor, no table-based QR ordering) -- so on
    token_billing.html, only the low-stock/system badge is expected to
    ever actually render for them. This is exactly the "for QSR mode I
    don't need that" distinction the badges' own role/feature gates
    already handle correctly, not something token_billing.html needed to
    special-case itself.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="QSR Badge Tenant", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(
            username="qsr_badge_mgr", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="open",
        )
        self.client = Client()
        self.client.login(username="qsr_badge_mgr", password="pw")

    def test_system_alerts_badge_shows_for_qsr_manager(self):
        resp = self.client.get(reverse("token-bill", args=[self.order.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="notif-badge-system"', resp.content.decode())

    def test_waiter_call_badge_does_not_show_for_qsr_tenant(self):
        """cafe/QSR tenant types don't have waiter_call by default -- the
        badge gate hides it correctly rather than showing a dead link."""
        resp = self.client.get(reverse("token-bill", args=[self.order.id]))
        self.assertNotIn('id="notif-badge-waiter"', resp.content.decode())
