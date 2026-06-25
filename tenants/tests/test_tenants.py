from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from tenants.models import Tenant, Outlet, PrintProfile, TenantFeatureOverride, TenantFeatureAuditLog
from accounts.models import User
from core.features import has_feature


class TenantModelTest(TestCase):

    def test_create_tenant(self):

        tenant = Tenant.objects.create(
            name="Pizza Hut",
            slug="pizza-hut"
        )

        self.assertEqual(tenant.name, "Pizza Hut")
        self.assertTrue(tenant.is_active)


class OutletModelTest(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(
            name="Dominos",
            slug="dominos"
        )

    def test_create_outlet(self):

        outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="MG Road"
        )

        self.assertEqual(outlet.name, "MG Road")

    def test_unique_outlet_per_tenant(self):

        Outlet.objects.create(
            tenant=self.tenant,
            name="Indiranagar"
        )

        with self.assertRaises(Exception):

            Outlet.objects.create(
                tenant=self.tenant,
                name="Indiranagar"
            )


class TenantSlugTest(TestCase):

    def test_slug_auto_generated_from_name(self):
        tenant = Tenant.objects.create(name="Burger Palace")
        self.assertEqual(tenant.slug, "burger-palace")

    def test_slug_unique_when_name_conflicts(self):
        t1 = Tenant.objects.create(name="Green Garden")
        t2 = Tenant.objects.create(name="Green Garden Unique")
        self.assertNotEqual(t1.slug, t2.slug)

    def test_tenant_type_defaults_to_fine_dining(self):
        tenant = Tenant.objects.create(name="Default Type Place")
        self.assertEqual(tenant.tenant_type, "fine_dining")

    def test_subscription_status_defaults_to_trial(self):
        tenant = Tenant.objects.create(name="Trial Restaurant")
        self.assertEqual(tenant.subscription_status, "trial")


class TenantFeatureFlagTest(TestCase):

    def setUp(self):
        self.fine_dining = Tenant.objects.create(
            name="Fine Diner",
            tenant_type="fine_dining"
        )
        self.franchise = Tenant.objects.create(
            name="Franchise QSR",
            tenant_type="franchise"
        )
        self.cafe = Tenant.objects.create(
            name="Corner Cafe",
            tenant_type="cafe"
        )

    def test_fine_dining_has_floor_plan(self):
        self.assertTrue(has_feature(self.fine_dining, "floor_plan"))

    def test_fine_dining_has_reservations(self):
        self.assertTrue(has_feature(self.fine_dining, "reservations"))

    def test_franchise_has_token_system(self):
        self.assertTrue(has_feature(self.franchise, "token_system"))

    def test_franchise_does_not_have_floor_plan(self):
        self.assertFalse(has_feature(self.franchise, "floor_plan"))

    def test_cafe_has_token_system(self):
        self.assertTrue(has_feature(self.cafe, "token_system"))

    def test_cafe_does_not_have_reservations(self):
        self.assertFalse(has_feature(self.cafe, "reservations"))

    def test_none_tenant_returns_false(self):
        self.assertFalse(has_feature(None, "floor_plan"))

    def test_unknown_feature_returns_false(self):
        self.assertFalse(has_feature(self.fine_dining, "nonexistent_feature_xyz"))

    def test_razorpay_gateway_off_by_default_for_every_tenant_type(self):
        # Custom-only feature — deliberately absent from every TENANT_FEATURES
        # list, so it must be False until a TenantFeatureOverride enables it.
        self.assertFalse(has_feature(self.fine_dining, "razorpay_gateway"))
        self.assertFalse(has_feature(self.franchise, "razorpay_gateway"))
        self.assertFalse(has_feature(self.cafe, "razorpay_gateway"))

    def test_whatsapp_receipts_off_by_default_for_every_tenant_type(self):
        self.assertFalse(has_feature(self.fine_dining, "whatsapp_receipts"))
        self.assertFalse(has_feature(self.franchise, "whatsapp_receipts"))
        self.assertFalse(has_feature(self.cafe, "whatsapp_receipts"))


class TenantFeatureOverrideTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Override Cafe",
            tenant_type="cafe"
        )

    def test_override_can_enable_feature_not_in_defaults(self):
        # cafe does not have reservations by default
        self.assertFalse(has_feature(self.tenant, "reservations"))
        TenantFeatureOverride.objects.create(
            tenant=self.tenant,
            feature="reservations",
            enabled=True
        )
        # bust the per-instance cache
        if hasattr(self.tenant, "_feature_overrides"):
            del self.tenant._feature_overrides
        self.assertTrue(has_feature(self.tenant, "reservations"))

    def test_override_can_disable_default_feature(self):
        # cafe has token_system by default
        self.assertTrue(has_feature(self.tenant, "token_system"))
        TenantFeatureOverride.objects.create(
            tenant=self.tenant,
            feature="token_system",
            enabled=False
        )
        if hasattr(self.tenant, "_feature_overrides"):
            del self.tenant._feature_overrides
        self.assertFalse(has_feature(self.tenant, "token_system"))

    def test_tenant_isolation_features_independent(self):
        tenant2 = Tenant.objects.create(
            name="Another Cafe",
            tenant_type="cafe"
        )
        TenantFeatureOverride.objects.create(
            tenant=self.tenant,
            feature="reservations",
            enabled=True
        )
        # tenant2 must NOT inherit tenant's override
        self.assertFalse(has_feature(tenant2, "reservations"))

    def test_override_can_enable_razorpay_gateway(self):
        self.assertFalse(has_feature(self.tenant, "razorpay_gateway"))
        TenantFeatureOverride.objects.create(
            tenant=self.tenant,
            feature="razorpay_gateway",
            enabled=True
        )
        if hasattr(self.tenant, "_feature_overrides"):
            del self.tenant._feature_overrides
        self.assertTrue(has_feature(self.tenant, "razorpay_gateway"))

        # a second, untouched tenant must not see it
        tenant2 = Tenant.objects.create(name="Untouched Cafe", tenant_type="cafe")
        self.assertFalse(has_feature(tenant2, "razorpay_gateway"))


class TenantFeatureAuditLogTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Audit Cafe", tenant_type="cafe")

    def test_create_audit_log_entry(self):
        entry = TenantFeatureAuditLog.objects.create(
            tenant=self.tenant,
            feature="razorpay_gateway",
            enabled=True,
            source="override",
            notes="Set by owner1",
        )
        self.assertEqual(entry.tenant, self.tenant)
        self.assertTrue(entry.enabled)
        self.assertEqual(entry.source, "override")

    def test_toggling_twice_appends_two_rows_not_one(self):
        # The point of this model: TenantFeatureOverride overwrites a single row,
        # but every change must leave its own permanent log entry.
        TenantFeatureAuditLog.objects.create(
            tenant=self.tenant, feature="razorpay_gateway", enabled=True, source="override",
        )
        TenantFeatureAuditLog.objects.create(
            tenant=self.tenant, feature="razorpay_gateway", enabled=False, source="default",
        )
        history = TenantFeatureAuditLog.objects.filter(
            tenant=self.tenant, feature="razorpay_gateway"
        ).order_by("changed_at")
        self.assertEqual(history.count(), 2)
        self.assertTrue(history[0].enabled)
        self.assertFalse(history[1].enabled)

    def test_tenant_deletion_cascades_audit_log(self):
        TenantFeatureAuditLog.objects.create(
            tenant=self.tenant, feature="razorpay_gateway", enabled=True, source="override",
        )
        self.tenant.delete()
        self.assertEqual(TenantFeatureAuditLog.objects.count(), 0)


# ── PrintProfile model tests ───────────────────────────────────────────────────

class PrintProfileModelTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Spice Garden", slug="spice-garden")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Branch")

    def _profile(self, name="Standard", **kw):
        defaults = dict(bill_inner_margin=4, kot_large_font=True, kot_show_total=True)
        defaults.update(kw)
        return PrintProfile.objects.create(tenant=self.tenant, name=name, **defaults)

    def test_create_print_profile(self):
        p = self._profile("Malenadu Standard")
        self.assertEqual(p.name, "Malenadu Standard")
        self.assertEqual(p.tenant, self.tenant)
        self.assertTrue(p.kot_large_font)
        self.assertTrue(p.kot_show_total)
        self.assertEqual(p.bill_inner_margin, 4)

    def test_str_includes_name_and_tenant(self):
        p = self._profile("Standard")
        self.assertIn("Standard", str(p))
        self.assertIn("Spice Garden", str(p))

    def test_name_unique_per_tenant(self):
        self._profile("Standard")
        with self.assertRaises(IntegrityError):
            self._profile("Standard")  # same tenant, same name

    def test_name_can_repeat_across_tenants(self):
        tenant2 = Tenant.objects.create(name="Other Cafe", slug="other-cafe")
        self._profile("Standard")
        # Same name, different tenant — must not raise
        PrintProfile.objects.create(
            tenant=tenant2, name="Standard",
            bill_inner_margin=4, kot_large_font=True, kot_show_total=True,
        )
        self.assertEqual(PrintProfile.objects.filter(name="Standard").count(), 2)

    def test_assign_profile_to_outlet(self):
        p = self._profile()
        self.outlet.print_profile = p
        self.outlet.save()
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.print_profile, p)

    def test_outlet_without_profile_is_valid(self):
        self.assertIsNone(self.outlet.print_profile)

    def test_deleting_profile_nullifies_outlet_fk(self):
        p = self._profile()
        self.outlet.print_profile = p
        self.outlet.save()
        p.delete()
        self.outlet.refresh_from_db()
        self.assertIsNone(self.outlet.print_profile)

    def test_profile_scoped_to_tenant(self):
        tenant2 = Tenant.objects.create(name="Rival", slug="rival")
        p1 = self._profile("A")
        PrintProfile.objects.create(
            tenant=tenant2, name="B",
            bill_inner_margin=0, kot_large_font=False, kot_show_total=False,
        )
        self.assertEqual(list(PrintProfile.objects.filter(tenant=self.tenant)), [p1])

    def test_defaults(self):
        p = PrintProfile.objects.create(tenant=self.tenant, name="Default")
        self.assertTrue(p.kot_large_font)
        self.assertTrue(p.kot_show_total)
        self.assertEqual(p.bill_inner_margin, 4)


class SubscriptionSuspensionMiddlewareTest(TestCase):
    """SubscriptionStatusMiddleware (core/middleware.py) — suspended tenants
    must lose access, except for superusers (support) and logout.

    Tenant resolution here goes through TenantMiddleware's real subdomain
    path (HTTP_HOST), not the ?tenant= dev fallback — Django's test runner
    forces settings.DEBUG=False during `manage.py test` regardless of what
    .env says, so the DEBUG-gated fallback never fires under `test`.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._allowed_hosts_override = override_settings(ALLOWED_HOSTS=["testserver", ".rasova.net"])
        cls._allowed_hosts_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._allowed_hosts_override.disable()
        super().tearDownClass()

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Suspend Test Cafe", subscription_status="suspended")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="suspend_owner", password="pass123",
            role="owner", tenant=self.tenant, outlet=self.outlet,
        )
        self.superuser = User.objects.create_superuser(
            username="suspend_admin", password="pass123", email="admin@rasova.net",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.host = f"{self.tenant.slug}.rasova.net"

    def _get(self, name="setup_wizard"):
        return self.client.get(reverse(name), HTTP_HOST=self.host)

    def test_suspended_blocks_authenticated_non_superuser(self):
        self.client.force_login(self.owner)
        response = self._get()
        self.assertEqual(response.status_code, 402)
        self.assertContains(response, "Suspended", status_code=402)

    def test_suspended_allows_superuser(self):
        self.client.force_login(self.superuser)
        response = self._get()
        self.assertEqual(response.status_code, 200)

    def test_active_tenant_is_not_blocked(self):
        self.tenant.subscription_status = "active"
        self.tenant.save()
        self.client.force_login(self.owner)
        response = self._get()
        self.assertEqual(response.status_code, 200)

    def test_trial_tenant_is_not_blocked(self):
        self.tenant.subscription_status = "trial"
        self.tenant.save()
        self.client.force_login(self.owner)
        response = self._get()
        self.assertEqual(response.status_code, 200)

    def test_anonymous_user_can_still_reach_login(self):
        response = self.client.get(reverse("login"), HTTP_HOST=self.host)
        self.assertEqual(response.status_code, 200)

    def test_suspended_user_can_still_logout(self):
        self.client.force_login(self.owner)
        response = self._get(name="logout")
        self.assertIn(response.status_code, [301, 302])