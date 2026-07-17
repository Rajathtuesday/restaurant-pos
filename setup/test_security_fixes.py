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
