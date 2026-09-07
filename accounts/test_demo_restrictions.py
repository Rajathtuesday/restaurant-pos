"""
Demo trailer mode: a session flag restricts what a public /live-demo/
visitor can do (menu editing, staff management, payment/outlet settings),
while the exact same demo_owner account reached via a private founder key
(?key=... on /live-demo/) is left completely unrestricted -- for doing a
live walkthrough yourself in front of an actual restaurant owner.

See md_files/rasova_demo_trailer_mode_plan_2026-09-07.html for the plan.

Run: python manage.py test accounts.test_demo_restrictions
"""
import json

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from accounts.models import User
from orders.scripts.demo_seed import create_or_reset_demo_tenant

FOUNDER_KEY = "test-founder-secret"


class TrailerModeFlagTests(TestCase):
    """demo_login itself -- who gets the wristband and who doesn't."""

    def setUp(self):
        create_or_reset_demo_tenant()
        self.client = Client()

    def test_public_link_sets_trailer_mode_on(self):
        self.client.get(reverse("live-demo"))
        self.assertTrue(self.client.session.get("demo_trailer_mode"))

    @override_settings(DEMO_FOUNDER_KEY=FOUNDER_KEY)
    def test_correct_founder_key_turns_trailer_mode_off(self):
        self.client.get(reverse("live-demo"), {"key": FOUNDER_KEY})
        self.assertFalse(self.client.session.get("demo_trailer_mode"))

    @override_settings(DEMO_FOUNDER_KEY=FOUNDER_KEY)
    def test_wrong_founder_key_still_gets_trailer_mode(self):
        self.client.get(reverse("live-demo"), {"key": "nope"})
        self.assertTrue(self.client.session.get("demo_trailer_mode"))

    def test_unset_founder_key_can_never_match_even_an_empty_key(self):
        """DEMO_FOUNDER_KEY defaults to '' -- ?key= (also empty) must not
        accidentally grant founder access."""
        self.client.get(reverse("live-demo"), {"key": ""})
        self.assertTrue(self.client.session.get("demo_trailer_mode"))


class MenuEditingBlockedInTrailerModeTests(TestCase):
    def setUp(self):
        self.tenant = create_or_reset_demo_tenant()
        self.client = Client()
        self.client.get(reverse("live-demo"))  # trailer mode on

    def test_create_menu_item_is_blocked(self):
        from menu.models import MenuCategory, MenuItem
        category = MenuCategory.objects.filter(tenant=self.tenant).first()
        res = self.client.post(
            reverse("create_menu_item"),
            data=json.dumps({"name": "Hacked Item", "price": "1", "category": category.id}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertFalse(res.json()["success"])
        self.assertFalse(MenuItem.objects.filter(tenant=self.tenant, name="Hacked Item").exists())

    def test_delete_category_is_blocked(self):
        from menu.models import MenuCategory
        category = MenuCategory.objects.filter(tenant=self.tenant).first()
        res = self.client.post(
            reverse("delete_category", args=[category.id]),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 403)
        self.assertTrue(MenuCategory.objects.filter(id=category.id).exists())


@override_settings(DEMO_FOUNDER_KEY=FOUNDER_KEY)
class FounderKeyBypassesRestrictionsTests(TestCase):
    """The whole point: you, doing a live call, must not be restricted."""

    def setUp(self):
        self.tenant = create_or_reset_demo_tenant()
        self.client = Client()
        self.client.get(reverse("live-demo"), {"key": FOUNDER_KEY})

    def test_create_menu_item_succeeds(self):
        from menu.models import MenuCategory, MenuItem
        category = MenuCategory.objects.filter(tenant=self.tenant).first()
        res = self.client.post(
            reverse("create_menu_item"),
            data=json.dumps({"name": "Founder Special", "price": "99", "category": category.id}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(MenuItem.objects.filter(tenant=self.tenant, name="Founder Special").exists())


class StaffAndSettingsBlockedInTrailerModeTests(TestCase):
    def setUp(self):
        self.tenant = create_or_reset_demo_tenant()
        self.client = Client()
        self.client.get(reverse("live-demo"))

    def test_creating_new_staff_is_blocked(self):
        res = self.client.post(reverse("setup_staff"), {
            "username": "sneaky_new_staff", "password": "whatever12345", "role": "waiter",
        })
        self.assertEqual(res.status_code, 302)
        self.assertFalse(User.objects.filter(username="sneaky_new_staff").exists())

    def test_toggle_staff_active_is_blocked(self):
        staff = User.objects.create_user(
            username="a_real_staff_member", password="x", role="waiter",
            tenant=self.tenant, outlet=self.tenant.outlets.first(),
        )
        res = self.client.post(
            reverse("toggle_staff_active", args=[staff.id]),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 403)
        staff.refresh_from_db()
        self.assertTrue(staff.is_active)

    def test_payment_settings_save_is_blocked(self):
        from setup.models import PaymentConfig
        outlet = self.tenant.outlets.first()
        config, _ = PaymentConfig.for_outlet(outlet, self.tenant)
        res = self.client.post(reverse("setup_payment_methods"), {
            "methods": ["cash", "upi"], "upi_id": "attacker@upi",
        })
        self.assertEqual(res.status_code, 302)
        config.refresh_from_db()
        self.assertFalse(config.upi_id)

    def test_outlet_settings_save_is_blocked(self):
        outlet = self.tenant.outlets.first()
        original_name = outlet.name
        res = self.client.post(reverse("outlet_settings"), {
            "outlet_name": "Definitely Not Demo Bistro",
            "po_vendor_email_enabled": "on",
        })
        self.assertEqual(res.status_code, 302)
        outlet.refresh_from_db()
        self.assertEqual(outlet.name, original_name)
        self.assertFalse(outlet.po_vendor_email_enabled)


class DemoBannerTests(TestCase):
    def setUp(self):
        create_or_reset_demo_tenant()
        self.client = Client()

    def test_banner_shows_for_a_trailer_mode_visitor(self):
        self.client.get(reverse("live-demo"))
        res = self.client.get("/tables/")
        # Searches for the actual rendered tag, not just the id name -- the
        # CSS rule for #demo-trailer-banner is always present in <style>
        # regardless of trailer mode, so a bare substring match would pass
        # even when the banner itself never renders.
        self.assertContains(res, 'id="demo-trailer-banner"')

    @override_settings(DEMO_FOUNDER_KEY=FOUNDER_KEY)
    def test_banner_is_absent_for_the_founder_key(self):
        self.client.get(reverse("live-demo"), {"key": FOUNDER_KEY})
        res = self.client.get("/tables/")
        self.assertNotContains(res, 'id="demo-trailer-banner"')

    def test_banner_is_absent_for_a_real_unrelated_tenants_own_user(self):
        from tenants.models import Tenant
        real_tenant = Tenant.objects.create(name="A Real Restaurant")
        outlet = real_tenant.outlets.create(name="Main")
        User.objects.create_user(
            username="real_owner", password="realpass12345", role="owner",
            tenant=real_tenant, outlet=outlet,
        )
        self.client.login(username="real_owner", password="realpass12345")
        res = self.client.get("/tables/")
        self.assertNotContains(res, 'id="demo-trailer-banner"')


class ScheduledResetTaskTests(TestCase):
    def test_reset_task_runs_the_real_reset_logic(self):
        from orders.tasks import reset_demo_tenant_task
        from orders.models import Order, Table

        tenant = create_or_reset_demo_tenant()
        outlet = tenant.outlets.first()
        table = Table.objects.filter(tenant=tenant).first()
        Order.objects.create(tenant=tenant, outlet=outlet, table=table, status="closed")
        self.assertEqual(Order.objects.filter(tenant=tenant).count(), 3)

        reset_demo_tenant_task()
        self.assertEqual(Order.objects.filter(tenant=tenant).count(), 2)
