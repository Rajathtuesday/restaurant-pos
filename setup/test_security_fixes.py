"""
Regression tests for the critical authorization fixes in setup/.

Covers:
  1. setup_payment_methods — a cashier (default role) can no longer reach the
     Razorpay/UPI credentials page (the payment-fraud hole).
  2. setup_staff — an owner cannot mint another "owner" via the staff form
     (role must be in the assignable set).
  3. onboarding_wizard — a low-privilege staff account can't POST wizard steps
     (name/slug rewrite, staff creation, UPI change).

Run: python manage.py test setup.test_security_fixes
"""
from django.test import TestCase, Client
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse

from accounts.models import User
from tenants.models import Tenant, Outlet


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Setup Tenant", slug="setup-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="owner1", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.cashier = User.objects.create_user(
            username="cashier1", password="pw", role="cashier",
            tenant=self.tenant, outlet=self.outlet,
        )


class PaymentMethodsRoleGateTest(_Base):
    def test_cashier_blocked_from_payment_config(self):
        client = Client()
        client.force_login(self.cashier)
        resp = client.get(reverse("setup_payment_methods"))
        # Non-owner/manager gets redirected away, not the config page.
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/", resp["Location"])

    def test_owner_allowed(self):
        client = Client()
        client.force_login(self.owner)
        resp = client.get(reverse("setup_payment_methods"))
        self.assertEqual(resp.status_code, 200)


class StaffRoleValidationTest(_Base):
    def test_owner_cannot_create_another_owner(self):
        client = Client()
        client.force_login(self.owner)
        client.post(reverse("setup_staff"), data={
            "username": "sneaky_owner",
            "password": "pw12345",
            "role": "owner",   # must be rejected — not an assignable role
        })
        self.assertFalse(User.objects.filter(username="sneaky_owner").exists())

    def test_owner_can_create_cashier(self):
        client = Client()
        client.force_login(self.owner)
        client.post(reverse("setup_staff"), data={
            "username": "new_cashier",
            "password": "pw12345",
            "role": "cashier",
        })
        created = User.objects.filter(username="new_cashier").first()
        self.assertIsNotNone(created)
        self.assertEqual(created.role, "cashier")


class OnboardingRoleGateTest(_Base):
    def setUp(self):
        super().setUp()
        self.waiter = User.objects.create_user(
            username="waiter_ob", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )

    def test_waiter_cannot_create_owner_via_onboarding_step3(self):
        client = Client()
        client.force_login(self.waiter)
        resp = client.post(
            reverse("onboarding_wizard") + "?step=3",
            data={"username": "ob_owner", "password": "pw12345", "role": "owner"},
        )
        # Redirected away by the role gate; no user created.
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(User.objects.filter(username="ob_owner").exists())


class ResetStaffPasswordTest(_Base):
    """
    Regression tests for reset_staff_password (setup/views/core_views.py).
    Added after a real incident where a staff member's password/lockout
    could only be fixed via direct server SSH access.
    """

    def setUp(self):
        super().setUp()
        self.waiter = User.objects.create_user(
            username="waiter1", password="old-password", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Other Main")
        self.other_owner = User.objects.create_user(
            username="other_owner", password="pw", role="owner",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )
        self.other_waiter = User.objects.create_user(
            username="other_waiter", password="pw", role="waiter",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )

    def _post(self, client, user_id, password):
        import json
        return client.post(
            reverse("reset_staff_password", args=[user_id]),
            data=json.dumps({"password": password}),
            content_type="application/json",
        )

    def test_owner_can_reset_staff_password(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "brand-new-password")
        self.assertEqual(resp.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertTrue(self.waiter.check_password("brand-new-password"))

    def test_cashier_cannot_reset_password(self):
        client = Client()
        client.force_login(self.cashier)
        resp = self._post(client, self.waiter.id, "brand-new-password")
        self.assertEqual(resp.status_code, 403)  # role_required (decorator) blocks with 403
        self.waiter.refresh_from_db()
        self.assertTrue(self.waiter.check_password("old-password"))

    def test_cannot_reset_owner_password(self):
        client = Client()
        client.force_login(self.owner)
        second_owner = User.objects.create_user(
            username="owner2", password="orig-pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        resp = self._post(client, second_owner.id, "hijacked-password")
        self.assertEqual(resp.status_code, 403)
        second_owner.refresh_from_db()
        self.assertTrue(second_owner.check_password("orig-pw"))

    def test_cannot_reset_password_across_tenants(self):
        # An owner in tenant A must not be able to reset a user's password
        # in tenant B by guessing/incrementing the target user's id.
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.other_waiter.id, "cross-tenant-hijack")
        self.assertEqual(resp.status_code, 404)
        self.other_waiter.refresh_from_db()
        self.assertTrue(self.other_waiter.check_password("pw"))

    def test_password_too_short_rejected(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "short")
        self.assertEqual(resp.status_code, 400)
        self.waiter.refresh_from_db()
        self.assertTrue(self.waiter.check_password("old-password"))

    def test_reset_clears_axes_lockout(self):
        from axes.utils import reset as axes_reset
        from axes.models import AccessAttempt

        AccessAttempt.objects.create(
            username=self.waiter.username,
            ip_address="127.0.0.1",
            failures_since_start=5,
        )
        self.assertTrue(AccessAttempt.objects.filter(username=self.waiter.username).exists())

        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "brand-new-password")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(AccessAttempt.objects.filter(username=self.waiter.username).exists())


class ToggleStaffActiveTest(_Base):
    """
    Regression tests for toggle_staff_active — deactivation was chosen over
    hard delete specifically because StaffSchedule.staff and
    CashSession.opened_by both CASCADE on the user FK.
    """

    def setUp(self):
        super().setUp()
        self.waiter = User.objects.create_user(
            username="waiter1", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Other Main")
        self.other_waiter = User.objects.create_user(
            username="other_waiter", password="pw", role="waiter",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )

    def _toggle(self, client, user_id):
        return client.post(
            reverse("toggle_staff_active", args=[user_id]),
            content_type="application/json",
        )

    def test_owner_can_deactivate_and_reactivate(self):
        client = Client()
        client.force_login(self.owner)

        resp = self._toggle(client, self.waiter.id)
        self.assertEqual(resp.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertFalse(self.waiter.is_active)

        resp = self._toggle(client, self.waiter.id)
        self.assertEqual(resp.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertTrue(self.waiter.is_active)

    def test_cashier_cannot_toggle(self):
        client = Client()
        client.force_login(self.cashier)
        resp = self._toggle(client, self.waiter.id)
        self.assertEqual(resp.status_code, 403)
        self.waiter.refresh_from_db()
        self.assertTrue(self.waiter.is_active)

    def test_cannot_deactivate_owner(self):
        client = Client()
        client.force_login(self.owner)
        second_owner = User.objects.create_user(
            username="owner2", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        resp = self._toggle(client, second_owner.id)
        self.assertEqual(resp.status_code, 403)
        second_owner.refresh_from_db()
        self.assertTrue(second_owner.is_active)

    def test_cannot_toggle_across_tenants(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._toggle(client, self.other_waiter.id)
        self.assertEqual(resp.status_code, 404)
        self.other_waiter.refresh_from_db()
        self.assertTrue(self.other_waiter.is_active)

    def test_deactivation_kills_active_session(self):
        # The waiter logs in for real (creates an actual session), then the
        # owner deactivates them — that session must die immediately, not
        # just block future logins.
        staff_client = Client()
        logged_in = staff_client.login(username="waiter1", password="pw")
        self.assertTrue(logged_in)

        # Confirm the session actually works before deactivation. waiter
        # isn't owner/manager so setup_wizard redirects to /dashboard/, not
        # to login — that redirect target is what proves the session (as
        # opposed to the role) is what's being checked here.
        resp = staff_client.get(reverse("setup_wizard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/", resp["Location"])

        owner_client = Client()
        owner_client.force_login(self.owner)
        self._toggle(owner_client, self.waiter.id)

        # Same session cookie, now must be logged out.
        resp = staff_client.get(reverse("setup_wizard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])

    def test_deactivated_user_cannot_log_in(self):
        # Client.login() shortcuts around the real login view and doesn't
        # pass a request into authenticate(), which axes' backend requires —
        # POSTing to the actual login view is what a real browser attempt
        # looks like, and is what actually needs to reject this.
        self.waiter.is_active = False
        self.waiter.save(update_fields=["is_active"])

        client = Client()
        client.post(reverse("login"), data={"username": "waiter1", "password": "pw"})
        resp = client.get(reverse("setup_wizard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp["Location"])


class EditStaffRoleTest(_Base):
    """
    Regression tests for edit_staff_role — role was previously set-once at
    account creation with no way to change it later short of direct DB/admin
    access.
    """

    def setUp(self):
        super().setUp()
        self.waiter = User.objects.create_user(
            username="waiter1", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Other Main")
        self.other_owner = User.objects.create_user(
            username="other_owner", password="pw", role="owner",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )
        self.other_waiter = User.objects.create_user(
            username="other_waiter", password="pw", role="waiter",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )

    def _post(self, client, user_id, role):
        import json
        return client.post(
            reverse("edit_staff_role", args=[user_id]),
            data=json.dumps({"role": role}),
            content_type="application/json",
        )

    def test_owner_can_change_waiter_to_captain(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "captain")
        self.assertEqual(resp.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.role, "captain")

    def test_cashier_cannot_edit_role(self):
        client = Client()
        client.force_login(self.cashier)
        resp = self._post(client, self.waiter.id, "captain")
        self.assertEqual(resp.status_code, 403)  # role_required (decorator) blocks with 403
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.role, "waiter")

    def test_cannot_set_role_to_owner(self):
        # Same privilege-escalation boundary as staff creation — role must
        # come from ASSIGNABLE_STAFF_ROLES, never trusted verbatim from POST.
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "owner")
        self.assertEqual(resp.status_code, 400)
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.role, "waiter")

    def test_cannot_set_role_to_agent(self):
        # "agent" is a Rasova-internal sales role, not assignable by a tenant.
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "agent")
        self.assertEqual(resp.status_code, 400)
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.role, "waiter")

    def test_cannot_edit_owner_role(self):
        client = Client()
        client.force_login(self.owner)
        second_owner = User.objects.create_user(
            username="owner2", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        resp = self._post(client, second_owner.id, "manager")
        self.assertEqual(resp.status_code, 403)
        second_owner.refresh_from_db()
        self.assertEqual(second_owner.role, "owner")

    def test_cannot_edit_role_across_tenants(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.other_waiter.id, "manager")
        self.assertEqual(resp.status_code, 404)
        self.other_waiter.refresh_from_db()
        self.assertEqual(self.other_waiter.role, "waiter")

    def test_setting_same_role_rejected(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "waiter")
        self.assertEqual(resp.status_code, 400)

    def test_role_change_takes_effect_on_next_request_without_relogin(self):
        # Unlike is_active, role isn't checked at authenticate() — it's read
        # fresh from the DB on every request via role_required, so a demoted
        # manager should lose access on their very next click, no forced
        # logout needed the way toggle_staff_active needs one.
        manager = User.objects.create_user(
            username="manager1", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        manager_client = Client()
        self.assertTrue(manager_client.login(username="manager1", password="pw"))

        resp = manager_client.get(reverse("setup_payment_methods"))
        self.assertEqual(resp.status_code, 200)

        owner_client = Client()
        owner_client.force_login(self.owner)
        resp = self._post(owner_client, manager.id, "waiter")
        self.assertEqual(resp.status_code, 200)

        # Same session cookie as before, no re-login — role gate now blocks.
        resp = manager_client.get(reverse("setup_payment_methods"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/dashboard/", resp["Location"])


class EditStaffOutletTest(_Base):
    """
    Regression tests for edit_staff_outlet — moving a staff member between
    a tenant's outlets previously required direct DB/admin access; outlet was
    only ever set once, at account creation.
    """

    def setUp(self):
        super().setUp()
        self.second_outlet = Outlet.objects.create(tenant=self.tenant, name="Second Branch")
        self.waiter = User.objects.create_user(
            username="waiter1", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.other_tenant = Tenant.objects.create(name="Other Tenant", slug="other-tenant-2")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Other Main")
        self.other_owner = User.objects.create_user(
            username="other_owner", password="pw", role="owner",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )
        self.other_waiter = User.objects.create_user(
            username="other_waiter", password="pw", role="waiter",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )

    def _post(self, client, user_id, outlet_id):
        import json
        return client.post(
            reverse("edit_staff_outlet", args=[user_id]),
            data=json.dumps({"outlet_id": outlet_id}),
            content_type="application/json",
        )

    def test_owner_can_move_waiter_to_second_outlet(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, self.second_outlet.id)
        self.assertEqual(resp.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.outlet_id, self.second_outlet.id)

    def test_cashier_cannot_edit_outlet(self):
        client = Client()
        client.force_login(self.cashier)
        resp = self._post(client, self.waiter.id, self.second_outlet.id)
        self.assertEqual(resp.status_code, 403)  # role_required (decorator) blocks with 403
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.outlet_id, self.outlet.id)

    def test_cannot_move_to_another_tenants_outlet(self):
        # Same privilege boundary as edit_staff_role: the posted outlet_id
        # must belong to the acting user's own tenant, never trusted verbatim.
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, self.other_outlet.id)
        self.assertEqual(resp.status_code, 400)
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.outlet_id, self.outlet.id)

    def test_cannot_edit_owner_outlet(self):
        client = Client()
        client.force_login(self.owner)
        second_owner = User.objects.create_user(
            username="owner2", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        resp = self._post(client, second_owner.id, self.second_outlet.id)
        self.assertEqual(resp.status_code, 403)
        second_owner.refresh_from_db()
        self.assertEqual(second_owner.outlet_id, self.outlet.id)

    def test_cannot_edit_outlet_across_tenants(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.other_waiter.id, self.second_outlet.id)
        self.assertEqual(resp.status_code, 404)
        self.other_waiter.refresh_from_db()
        self.assertEqual(self.other_waiter.outlet_id, self.other_outlet.id)

    def test_setting_same_outlet_rejected(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, self.outlet.id)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_outlet_id_rejected(self):
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "not-a-real-id")
        self.assertEqual(resp.status_code, 400)
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.outlet_id, self.outlet.id)


class SetupStaffQueryCountTest(_Base):
    """
    Regression test for the confirmed N+1 in setup_staff: the page template
    renders member.outlet.name per row, and without select_related("outlet")
    each additional staff member would cost one more query. The query count
    for the GET request must stay flat as staff count grows, not scale with
    it -- so this compares query counts at two different staff counts rather
    than asserting one hardcoded number (which would be fragile against
    unrelated middleware/session query changes).
    """

    def _query_count(self):
        client = Client()
        client.force_login(self.owner)
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(reverse("setup_staff"))
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_query_count_does_not_scale_with_staff_count(self):
        baseline = self._query_count()

        for i in range(10):
            User.objects.create_user(
                username=f"extra_staff_{i}", password="pw", role="waiter",
                tenant=self.tenant, outlet=self.outlet,
            )

        with_more_staff = self._query_count()

        self.assertEqual(
            baseline, with_more_staff,
            "setup_staff query count grew with staff count -- the "
            "select_related(\"outlet\") N+1 fix appears to have regressed."
        )
