# accounts/tests.py

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from tenants.models import Tenant, Outlet

User = get_user_model()


class UserModelTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Restaurant")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Branch"
        )

    def test_create_user_with_role(self):

        user = User.objects.create_user(
            username="waiter1",
            password="testpass123",
            role="waiter",
            tenant=self.tenant,
            outlet=self.outlet
        )

        self.assertEqual(user.role, "waiter")
        self.assertEqual(user.tenant, self.tenant)
        self.assertEqual(user.outlet, self.outlet)


class LoginViewTest(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Test Restaurant")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Branch"
        )

        self.user = User.objects.create_user(
            username="chef1",
            password="testpass123",
            role="chef",
            tenant=self.tenant,
            outlet=self.outlet
        )

    def test_login_success(self):

        response = self.client.post(
            reverse("login"),
            {
                "username": "chef1",
                "password": "testpass123"
            }
        )

        self.assertEqual(response.status_code, 302)


class DashboardPermissionTest(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Test Restaurant")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Branch"
        )

        self.owner = User.objects.create_user(
            username="owner1",
            password="pass123",
            role="owner",
            tenant=self.tenant,
            outlet=self.outlet
        )

        self.chef = User.objects.create_user(
            username="chef1",
            password="pass123",
            role="chef",
            tenant=self.tenant,
            outlet=self.outlet
        )

    def test_owner_can_access_dashboard(self):

        self.client.login(username="owner1", password="pass123")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)

    def test_chef_cannot_access_dashboard(self):

        self.client.login(username="chef1", password="pass123")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 403)

    def test_waiter_cannot_access_dashboard(self):

        waiter = User.objects.create_user(
            username="waiter1",
            password="pass123",
            role="waiter",
            tenant=self.tenant,
            outlet=self.outlet
        )

        self.client.login(username="waiter1", password="pass123")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 403)


class LoginErrorTest(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Error Test Restaurant")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Branch"
        )

        self.user = User.objects.create_user(
            username="testuser",
            password="correct_password",
            role="cashier",
            tenant=self.tenant,
            outlet=self.outlet
        )

    def test_wrong_password_stays_on_login_page(self):

        response = self.client.post(
            reverse("login"),
            {"username": "testuser", "password": "wrong_password"}
        )

        self.assertEqual(response.status_code, 200)

    def test_wrong_password_shows_error_message(self):

        response = self.client.post(
            reverse("login"),
            {"username": "testuser", "password": "wrong_password"}
        )

        msgs = [str(m) for m in response.context["messages"]]
        self.assertTrue(
            any("Invalid" in m or "invalid" in m for m in msgs),
            f"Expected error message, got: {msgs}"
        )

    def test_nonexistent_user_stays_on_login_page(self):

        response = self.client.post(
            reverse("login"),
            {"username": "nobody", "password": "whatever"}
        )

        self.assertEqual(response.status_code, 200)


class LogoutViewTest(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Logout Restaurant")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main Branch"
        )

        self.user = User.objects.create_user(
            username="logout_user",
            password="pass123",
            role="cashier",
            tenant=self.tenant,
            outlet=self.outlet
        )

    def test_logout_redirects(self):

        self.client.login(username="logout_user", password="pass123")

        response = self.client.get(reverse("logout"))

        self.assertIn(response.status_code, [301, 302])

    def test_logout_redirects_to_login(self):

        self.client.login(username="logout_user", password="pass123")

        response = self.client.get(reverse("logout"))

        self.assertIn("login", response.get("Location", "").lower())


class NoStoreForAuthenticatedResponsesMiddlewareTest(TestCase):
    """
    Pressing the browser's Back button after logout could show the
    last-rendered authenticated page straight from the browser's own
    Back/Forward cache, without ever recontacting the server -- the
    account IS genuinely logged out (logout_view destroys the session
    correctly), the browser just never re-asked. Only a handful of views
    (dashboard, tokens, billing_core) told the browser not to keep a
    snapshot at all (@never_cache); every other page had no such header.
    core.middleware.NoStoreForAuthenticatedResponsesMiddleware applies
    Cache-Control: no-store centrally, to every authenticated response.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="NoStore Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.manager = User.objects.create_user(
            username="nostore_mgr", password="pwd",
            role="manager", tenant=self.tenant, outlet=self.outlet,
        )

    def test_authenticated_response_gets_no_store(self):
        self.client.login(username="nostore_mgr", password="pwd")
        response = self.client.get("/inventory/board/")
        self.assertIn("no-store", response.get("Cache-Control", ""))

    def test_unauthenticated_response_is_untouched(self):
        response = self.client.get(reverse("login"))
        self.assertNotIn("no-store", response.get("Cache-Control", ""))


# ---------------------------------------------------------------------------
# Schema-review additions
# ---------------------------------------------------------------------------

class UserSchemaReviewTest(TestCase):
    """Tests for schema-review fixes applied to the accounts.User model."""

    def test_kitchen_role_in_choices(self):
        """role_required('kitchen') is used in print_views — 'kitchen' must be in ROLE_CHOICES."""
        role_values = [r[0] for r in User.ROLE_CHOICES]
        self.assertIn("kitchen", role_values)

    def test_all_expected_roles_present(self):
        role_values = [r[0] for r in User.ROLE_CHOICES]
        for role in ("owner", "manager", "agent", "cashier", "captain", "waiter", "chef", "kitchen"):
            self.assertIn(role, role_values)

    def test_outlet_index_present(self):
        field_sets = [tuple(idx.fields) for idx in User._meta.indexes]
        self.assertIn(("outlet",), field_sets)

    def test_tenant_role_composite_index_present(self):
        field_sets = [tuple(idx.fields) for idx in User._meta.indexes]
        self.assertIn(("tenant", "role"), field_sets)

    def test_kitchen_user_can_be_created(self):
        tenant = Tenant.objects.create(name="Kitchen Test")
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        user = User.objects.create_user(
            username="kitchenstaff1", password="pass",
            role="kitchen", tenant=tenant, outlet=outlet
        )
        self.assertEqual(user.role, "kitchen")

    def test_captain_role_in_choices(self):
        role_values = [r[0] for r in User.ROLE_CHOICES]
        self.assertIn("captain", role_values)

    def test_captain_in_assignable_staff_roles(self):
        self.assertIn("captain", User.ASSIGNABLE_STAFF_ROLES)
        # Owner/agent must never be self-assignable through a staff form.
        self.assertNotIn("owner", User.ASSIGNABLE_STAFF_ROLES)
        self.assertNotIn("agent", User.ASSIGNABLE_STAFF_ROLES)

    def test_captain_user_can_be_created(self):
        tenant = Tenant.objects.create(name="Captain Test")
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        user = User.objects.create_user(
            username="captain1", password="pass",
            role="captain", tenant=tenant, outlet=outlet
        )
        self.assertEqual(user.role, "captain")


class CaptainLoginRedirectTest(TestCase):
    """_role_path() must route captain like a floor-service role, not fall
    through to whatever the chain's default happens to be today."""

    def setUp(self):
        self.fine_dining_tenant = Tenant.objects.create(
            name="Fine Dining Captain Test", tenant_type="fine_dining"
        )
        self.fine_dining_outlet = Outlet.objects.create(
            tenant=self.fine_dining_tenant, name="Main"
        )
        self.franchise_tenant = Tenant.objects.create(
            name="Franchise Captain Test", tenant_type="franchise"
        )
        self.franchise_outlet = Outlet.objects.create(
            tenant=self.franchise_tenant, name="Main"
        )

    def test_captain_redirects_to_tables_for_fine_dining(self):
        User.objects.create_user(
            username="captain_fd", password="testpass123", role="captain",
            tenant=self.fine_dining_tenant, outlet=self.fine_dining_outlet,
        )
        response = self.client.post(
            reverse("login"), {"username": "captain_fd", "password": "testpass123"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/tables/"))

    def test_captain_redirects_to_token_for_franchise(self):
        User.objects.create_user(
            username="captain_fr", password="testpass123", role="captain",
            tenant=self.franchise_tenant, outlet=self.franchise_outlet,
        )
        response = self.client.post(
            reverse("login"), {"username": "captain_fr", "password": "testpass123"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith("/token/"))


# ---------------------------------------------------------------------------
# toggle_feature_flag — feature gating + per-tenant audit history
# ---------------------------------------------------------------------------

class ToggleFeatureFlagAuditTest(TestCase):
    """
    Razorpay/WhatsApp (and every other custom-only feature) are toggled per-tenant
    through this view. Each toggle must leave a permanent TenantFeatureAuditLog row —
    TenantFeatureOverride itself only holds current state, not history.
    """

    def setUp(self):
        import json
        self.json = json
        self.tenant = Tenant.objects.create(name="Gated Cafe", tenant_type="cafe")
        self.superuser = User.objects.create_user(
            username="support1", password="pass123", is_superuser=True, is_staff=True,
        )
        self.staff_user = User.objects.create_user(
            username="owner1", password="pass123",
            role="owner", tenant=self.tenant,
        )

    def test_non_superuser_gets_403_and_writes_no_audit_log(self):
        from tenants.models import TenantFeatureAuditLog
        self.client.login(username="owner1", password="pass123")
        response = self.client.post(
            reverse("toggle_feature_flag"),
            data=self.json.dumps({
                "tenant_id": self.tenant.id, "feature": "razorpay_gateway", "enabled": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TenantFeatureAuditLog.objects.count(), 0)

    def test_enabling_writes_audit_log_entry(self):
        from tenants.models import TenantFeatureAuditLog
        self.client.login(username="support1", password="pass123")
        response = self.client.post(
            reverse("toggle_feature_flag"),
            data=self.json.dumps({
                "tenant_id": self.tenant.id, "feature": "razorpay_gateway", "enabled": True,
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        entry = TenantFeatureAuditLog.objects.get(tenant=self.tenant, feature="razorpay_gateway")
        self.assertTrue(entry.enabled)
        self.assertEqual(entry.source, "override")
        self.assertEqual(entry.changed_by, self.superuser)

    def test_toggling_on_then_off_appends_two_audit_rows(self):
        from tenants.models import TenantFeatureAuditLog
        self.client.login(username="support1", password="pass123")

        self.client.post(
            reverse("toggle_feature_flag"),
            data=self.json.dumps({
                "tenant_id": self.tenant.id, "feature": "whatsapp_receipts", "enabled": True,
            }),
            content_type="application/json",
        )
        self.client.post(
            reverse("toggle_feature_flag"),
            data=self.json.dumps({
                "tenant_id": self.tenant.id, "feature": "whatsapp_receipts", "enabled": False,
            }),
            content_type="application/json",
        )

        history = TenantFeatureAuditLog.objects.filter(
            tenant=self.tenant, feature="whatsapp_receipts"
        ).order_by("changed_at")
        # Both toggles must be preserved — not one row overwritten in place.
        self.assertEqual(history.count(), 2)
        self.assertTrue(history[0].enabled)
        self.assertFalse(history[1].enabled)

    def test_second_tenant_unaffected(self):
        from core.features import has_feature
        tenant2 = Tenant.objects.create(name="Other Cafe", tenant_type="cafe")
        self.client.login(username="support1", password="pass123")
        self.client.post(
            reverse("toggle_feature_flag"),
            data=self.json.dumps({
                "tenant_id": self.tenant.id, "feature": "razorpay_gateway", "enabled": True,
            }),
            content_type="application/json",
        )
        self.assertTrue(has_feature(self.tenant, "razorpay_gateway"))
        self.assertFalse(has_feature(tenant2, "razorpay_gateway"))


# ---------------------------------------------------------------------------
# _FEATURE_META completeness — the toggle UI's own list must not drift from
# the system's real, canonical list of features
# ---------------------------------------------------------------------------

class FeatureMetaCompletenessTest(TestCase):
    """
    core/features.py::get_all_known_features() (flattened from FEATURE_GROUPS)
    is the canonical list of every feature name the system actually enforces
    -- it's what core/context_processors.py uses to build `tenant_features`
    for every template. _FEATURE_META in accounts/views/feature_views.py is a
    SEPARATE, hand-maintained dict that's supposed to mirror it (adding UI
    label/icon/desc/group) for the superuser toggle screen at
    /settings/features/.

    These drifted: parcel_charge, composition_scheme, and counter_billing
    were all real, actively-enforced features (confirmed via has_feature()
    call sites and the tenant-config preset system) that were simply absent
    from _FEATURE_META -- a superuser could bulk-apply a preset that included
    them, but could never see or individually toggle just one of them on the
    actual toggle screen. This test would have caught that immediately, and
    stops it from silently happening again for the next new feature.
    """

    def test_feature_meta_covers_every_known_feature(self):
        from accounts.views.feature_views import _FEATURE_META
        from core.features import get_all_known_features

        missing = get_all_known_features() - set(_FEATURE_META.keys())
        self.assertEqual(
            missing, set(),
            f"_FEATURE_META is missing real feature(s): {missing} -- "
            "a superuser can't see or toggle these on /settings/features/ "
            "even though the system enforces them elsewhere."
        )