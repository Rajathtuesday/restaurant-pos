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


class SuperuserWhoBelongsToARestaurantTest(TestCase):
    """Production's superuser also belongs to a restaurant (Spice Garden).
    Every TenantScopedModel query is scoped to the signed-in user's own
    restaurant (core/models.py, TenantManager), so the panel saw none of
    another restaurant's stations or payment setup: on 28 Nov 2026
    /superuser/tenant/<a new pub>/ answered 500 (PaymentConfig.for_outlet:
    "already exists", then "does not exist"). The panel names the
    restaurant it manages instead (.for_tenant / .for_outlet)."""

    def setUp(self):
        from setup.models import KitchenStation, PaymentConfig
        from tenants.services.restaurant_setup import create_restaurant

        self.home, self.home_outlet, _ = create_restaurant(
            name="Home Pub", tenant_type="pub", owner_username="home_owner", owner_password="pw-123456")
        self.other, self.other_outlet, _ = create_restaurant(
            name="Other Pub", tenant_type="pub", owner_username="other_owner", owner_password="pw-123456")
        self.home_grill = KitchenStation.objects.create(tenant=self.home, outlet=self.home_outlet, name="Home Grill")
        self.other_grill = KitchenStation.objects.create(tenant=self.other, outlet=self.other_outlet, name="Other Grill")
        self.superuser = User.objects.create_user(
            username="rasova_su", password="pass", is_superuser=True, is_staff=True,
            tenant=self.home, outlet=self.home_outlet, role="owner")
        self.client = Client()
        self.client.force_login(self.superuser)
        self.url = reverse("superuser_tenant", args=[self.other.id])
        self.configs = PaymentConfig

    def test_the_other_restaurants_page_shows_its_own_rows(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("Other Grill", page)
        self.assertNotIn("Home Grill", page)
        self.assertEqual(self.configs.objects.for_outlet(self.other, self.other_outlet).count(), 1)

    def test_its_printer_and_payments_save_to_it(self):
        self.client.post(self.url, {"action": "update_printer", "station_id": self.other_grill.id,
                                    "printer_ip": "192.168.1.77", "printer_port": "9100",
                                    "paper_width_mm": "80", "cut_type": "partial"})
        self.client.post(self.url, {"action": "update_payment", "methods": ["cash", "upi"],
                                    "upi_id": "otherpub@upi"})
        self.other_grill.refresh_from_db()
        self.assertEqual(self.other_grill.printer_ip, "192.168.1.77")
        other = self.configs.objects.for_outlet(self.other, self.other_outlet).get()
        home = self.configs.objects.for_outlet(self.home, self.home_outlet).get()
        self.assertEqual((other.upi_id, other.card_enabled), ("otherpub@upi", False))
        self.assertNotEqual(home.upi_id, "otherpub@upi")

    def test_a_preset_applies_to_it(self):
        response = self.client.post(reverse("superuser_preset", args=[self.other.id]), {"preset": "pub"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
