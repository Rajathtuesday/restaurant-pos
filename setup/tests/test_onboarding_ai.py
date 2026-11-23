"""
Onboarding's menu step offers AI Import only to a restaurant that has the AI
menu import switched on (the walkthrough recording, 23 Nov 2026: a pub with
it off still saw the card, and a box, which has no internet, would too).

Run: python manage.py test setup.tests.test_onboarding_ai
"""
from django.test import TestCase

from accounts.models import User
from tenants.models import Outlet, Tenant, TenantFeatureOverride


class OnboardingAiCardTests(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="AI Card", tenant_type="pub")
        outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        owner = User.objects.create_user(username="ai_card_owner", password="pw", tenant=self.tenant,
                                         outlet=outlet, role="owner")
        self.client.force_login(owner)

    def step_two(self):
        return self.client.get("/setup/onboard/?step=2").content.decode()

    def test_switched_off_there_is_no_ai_card(self):
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="ai_menu_import", enabled=False)
        page = self.step_two()
        self.assertNotIn("ai-import-box", page)
        self.assertIn("Load Sample Menu", page)

    def test_switched_on_the_card_is_there(self):
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="ai_menu_import", enabled=True)
        self.assertIn("ai-import-box", self.step_two())
