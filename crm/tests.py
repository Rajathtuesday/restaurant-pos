"""
Smoke + access-control tests for the CRM app.

CRM touches loyalty points (money-adjacent), so at minimum we prove:
  - the dashboard and reservations load for allowed roles
  - disallowed roles get 403
  - anonymous users are redirected to login
  - one tenant cannot see another tenant's guests
"""

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from crm.models import Guest
from tenants.models import Outlet, Tenant


class CRMAccessControlTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="CRM Cafe", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="crm_owner", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.waiter = User.objects.create_user(
            username="crm_waiter", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="waiter",
        )

    def test_dashboard_loads_for_owner(self):
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_dashboard_forbidden_for_waiter(self):
        c = Client()
        c.force_login(self.waiter)
        resp = c.get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 403)

    def test_dashboard_redirects_anonymous(self):
        resp = Client().get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_reservations_loads_for_owner(self):
        c = Client()
        c.force_login(self.owner)
        resp = c.get(reverse("reservation-list"))
        self.assertEqual(resp.status_code, 200)


class CRMTenantIsolationTests(TestCase):

    def setUp(self):
        self.t_a = Tenant.objects.create(name="CRM Tenant A", tenant_type="fine_dining")
        self.o_a = Outlet.objects.create(tenant=self.t_a, name="A")
        self.t_b = Tenant.objects.create(name="CRM Tenant B", tenant_type="fine_dining")
        self.o_b = Outlet.objects.create(tenant=self.t_b, name="B")

        self.owner_a = User.objects.create_user(
            username="crm_owner_a", password="pass",
            tenant=self.t_a, outlet=self.o_a, role="owner",
        )
        # A guest that belongs to tenant B only
        Guest.objects.create(tenant=self.t_b, name="Bob B", phone="9000000001")

    def test_owner_cannot_see_other_tenants_guests(self):
        c = Client()
        c.force_login(self.owner_a)
        resp = c.get(reverse("crm-dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Bob B")
