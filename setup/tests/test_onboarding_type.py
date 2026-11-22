"""
Onboarding step 1 kept a pub a pub (22 Nov 2026).

The form's "Type of restaurant" list had Fine Dining, QSR and Cafe, and no
Pub: a pub owner who pressed Next on step 1 turned the pub into fine dining
(the browser posts the first option), and its liquor features went. The
list now comes from the model's choices, and only a real type is saved.

Run: python manage.py test setup.tests.test_onboarding_type
"""
from django.test import Client, TestCase

from accounts.models import User
from tenants.models import Outlet, Tenant


class OnboardingTypeTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Copper Tap", slug="ob-copper", tenant_type="pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(username="ob-owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.client = Client()
        self.client.force_login(self.owner)

    def test_a_pub_sees_pub_chosen(self):
        page = self.client.get("/setup/onboard/?step=1")
        self.assertContains(page, '<option value="pub" selected>')

    def test_every_kind_of_restaurant_is_on_the_list(self):
        page = self.client.get("/setup/onboard/?step=1").content.decode()
        for value in Tenant.TenantType.values:
            self.assertIn(f'<option value="{value}"', page, value)

    def test_step_one_keeps_the_pub_a_pub(self):
        self.client.post("/setup/onboard/?step=1", {"name": "Copper Tap", "tenant_type": "pub"})
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.tenant_type, "pub")

    def test_a_type_that_does_not_exist_is_not_saved(self):
        self.client.post("/setup/onboard/?step=1", {"name": "Copper Tap", "tenant_type": "casino"})
        self.tenant.refresh_from_db()
        self.assertEqual(self.tenant.tenant_type, "pub")
