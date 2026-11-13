"""
The setup checklist badge stays off the working screens.

Why: "Setup 4/5 complete" sat in the bottom corner of every page until setup
was done, and on the billing screen it covered the Bill and Print buttons
(desktop) and View Order (phone): seen on the screenshot sheet, 13 Nov 2026.
It is offered where an owner sets up: the dashboard, setup and menu pages.

Run: python manage.py test core.tests.test_setup_checklist_where
"""
from django.test import Client, SimpleTestCase, TestCase

from core.context_processors import shows_setup_checklist
from core.tests.test_tenant_walk import Restaurant


class WhereTest(SimpleTestCase):

    def test_the_pages_an_owner_sets_up_from(self):
        for path in ("/dashboard/", "/setup/", "/setup/tables/", "/menu/", "/menu/modifiers/"):
            self.assertTrue(shows_setup_checklist(path), path)

    def test_never_on_a_working_screen(self):
        for path in ("/billing/", "/bill/7/", "/tables/", "/kitchen/", "/token/", "/token/7/bill/",
                     "/live-orders/", "/menu/qr/"):
            self.assertFalse(shows_setup_checklist(path), path)


class RenderedTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.r = Restaurant("Alpha", "checklist-alpha")

    def has_widget(self, url):
        client = Client()
        client.force_login(self.r.owner)
        return 'id="setup-checklist-widget"' in client.get(url).content.decode()

    def test_billing_has_no_badge_and_the_dashboard_does(self):
        self.assertFalse(self.has_widget("/billing/"))
        self.assertFalse(self.has_widget("/tables/"))
        self.assertTrue(self.has_widget("/dashboard/"))
        self.assertTrue(self.has_widget("/setup/"))
