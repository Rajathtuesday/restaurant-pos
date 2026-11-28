"""
A page opened at another restaurant's address takes you to your own.

Why: one browser holds one login for every *.rasova.net address. On 28 Nov
2026 the owner signed in as Tuesdays from Spice Garden's page; Spice's page,
still open, then answered with a bare "403 Forbidden". The refusal itself is
right (one restaurant's login never reads another's rows), but for a person
opening a page it says nothing useful. Now they are taken to the same page
at their own restaurant's address, which says once why they are there. A
script's request (a poll, a form post) is still refused with the 403.

Run: python manage.py test core.tests.test_other_restaurants_address
"""
from django.test import Client, TestCase, override_settings

from core.tests.test_tenant_walk import Restaurant

NOTE = "That address belongs to another restaurant."
PAGE = {"HTTP_ACCEPT": "text/html,application/xhtml+xml,*/*;q=0.8"}


@override_settings(ALLOWED_HOSTS=[".rasova.net"], BASE_URL="https://rasova.net")
class OtherRestaurantsAddressTest(TestCase):

    def setUp(self):
        self.mine = Restaurant("Alpha", "addr-alpha")
        self.theirs = Restaurant("Beta", "addr-beta")
        self.client = Client()
        self.client.force_login(self.mine.owner)

    def at(self, restaurant):
        return f"{restaurant.tenant.slug}.rasova.net"

    def test_a_page_at_their_address_goes_to_the_same_page_at_mine(self):
        response = self.client.get("/menu/?q=dosa", HTTP_HOST=self.at(self.theirs), **PAGE)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"https://{self.at(self.mine)}/menu/?q=dosa")

    def test_my_page_says_why_once(self):
        self.client.get("/dashboard/", HTTP_HOST=self.at(self.theirs), **PAGE)
        first = self.client.get("/dashboard/", HTTP_HOST=self.at(self.mine), **PAGE).content.decode()
        self.assertIn(NOTE, first)
        self.assertIn(f"signed in to {self.mine.tenant.name}", first)
        self.assertNotIn(self.theirs.tenant.name, first)
        again = self.client.get("/dashboard/", HTTP_HOST=self.at(self.mine), **PAGE).content.decode()
        self.assertNotIn(NOTE, again)

    def test_a_scripts_request_there_is_still_refused(self):
        poll = self.client.get("/api/notifications/", HTTP_HOST=self.at(self.theirs), HTTP_ACCEPT="application/json")
        self.assertEqual(poll.status_code, 403)
        xhr = self.client.get("/dashboard/", HTTP_HOST=self.at(self.theirs),
                              HTTP_X_REQUESTED_WITH="XMLHttpRequest", **PAGE)
        self.assertEqual(xhr.status_code, 403)
        post = self.client.post("/menu/ai-import/", {"text": "x"}, HTTP_HOST=self.at(self.theirs), **PAGE)
        self.assertEqual(post.status_code, 403)

    def test_at_my_own_address_nothing_changes(self):
        response = self.client.get("/dashboard/", HTTP_HOST=self.at(self.mine), **PAGE)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(NOTE, response.content.decode())
