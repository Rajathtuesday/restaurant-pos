"""
Smoke + access-control tests for the Portal (internal Rasova staff ops panel).

Portal can create tenants and staff, so access must be locked to superusers.
We prove:
  - superuser loads the home panel
  - a normal tenant owner is forbidden (403)
  - anonymous is redirected to login
"""

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from tenants.models import Outlet, Tenant


class PortalAccessControlTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Portal Tenant", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

        self.superuser = User.objects.create_user(username="rasova_staff", password="pass")
        self.superuser.is_superuser = True
        self.superuser.is_staff = True
        self.superuser.save()

        self.owner = User.objects.create_user(
            username="portal_owner", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )

    def test_home_loads_for_superuser(self):
        c = Client()
        c.force_login(self.superuser)
        resp = c.get(reverse("portal:home"))
        self.assertEqual(resp.status_code, 200)

    def test_home_forbidden_for_normal_owner(self):
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("portal:home"))
        self.assertEqual(resp.status_code, 403)

    def test_home_redirects_anonymous(self):
        resp = Client().get(reverse("portal:home"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])
