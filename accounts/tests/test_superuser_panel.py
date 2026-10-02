"""
Who may open the superuser panel (/superuser/). It creates restaurants and
staff, so only Rasova's own superusers. These checks used to cover /portal/,
a second copy of this panel removed on 2 Oct 2026.

Run: python manage.py test accounts.tests.test_superuser_panel
"""
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from tenants.models import Outlet, Tenant


class SuperuserPanelAccessTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Panel Tenant", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.superuser = User.objects.create_user(username="rasova_staff", password="pass",
                                                  is_superuser=True, is_staff=True)
        self.owner = User.objects.create_user(username="panel_owner", password="pass",
                                              tenant=self.tenant, outlet=self.outlet, role="owner")

    def test_a_superuser_opens_it(self):
        c = Client()
        c.force_login(self.superuser)
        self.assertEqual(c.get(reverse("superuser_panel")).status_code, 200)
        self.assertEqual(c.get(reverse("superuser_tenant", args=[self.tenant.id])).status_code, 200)

    def test_a_restaurant_owner_is_refused(self):
        c = Client()
        c.force_login(self.owner)
        self.assertEqual(c.get(reverse("superuser_panel")).status_code, 403)
        self.assertEqual(c.get(reverse("superuser_tenant", args=[self.tenant.id])).status_code, 403)

    def test_anyone_logged_out_is_sent_to_login(self):
        resp = Client().get(reverse("superuser_panel"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_the_old_portal_address_is_gone(self):
        c = Client()
        c.force_login(self.superuser)
        self.assertEqual(c.get("/portal/").status_code, 404)
