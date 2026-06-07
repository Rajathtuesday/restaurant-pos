"""
Smoke + access-control tests for the Agency (sales-partner performance) app.

This dashboard exposes cross-tenant revenue/agent data, so it must be
superuser-only. We prove:
  - superuser loads the dashboard and the stats API
  - a normal tenant owner is forbidden (403)
  - anonymous is redirected to login
"""

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from tenants.models import Outlet, Tenant


class AgencyAccessControlTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Agency Tenant", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")

        self.superuser = User.objects.create_user(username="agency_admin", password="pass")
        self.superuser.is_superuser = True
        self.superuser.is_staff = True
        self.superuser.save()

        self.owner = User.objects.create_user(
            username="agency_owner", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )

    def test_dashboard_loads_for_superuser(self):
        c = Client()
        c.force_login(self.superuser)
        resp = c.get(reverse("agency_dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_forbidden_for_normal_owner(self):
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("agency_dashboard"))
        self.assertEqual(resp.status_code, 403)

    def test_dashboard_redirects_anonymous(self):
        resp = Client().get(reverse("agency_dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_stats_api_forbidden_for_normal_owner(self):
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("agency_stats_api"))
        self.assertEqual(resp.status_code, 403)
