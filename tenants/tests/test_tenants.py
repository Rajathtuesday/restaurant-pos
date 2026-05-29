from django.db import IntegrityError
from django.test import TestCase
from tenants.models import Tenant, Outlet, PrintProfile, TenantFeatureOverride
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