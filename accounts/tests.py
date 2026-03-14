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