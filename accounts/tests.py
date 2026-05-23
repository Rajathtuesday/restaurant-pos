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
        for role in ("owner", "manager", "agent", "cashier", "waiter", "chef", "kitchen"):
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