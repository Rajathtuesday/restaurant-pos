"""SearchIndexingMiddleware: only the marketing pages on rasova.net and the
guest menu on a restaurant's own subdomain may be indexed by search engines.

Search Console (6 Oct 2026) had indexed rasova.net/login/ and
spice.rasova.net/compare/ (a copy of the marketing page, served on every
subdomain), while the real rasova.net/compare/ waited unindexed.
"""
from django.test import TestCase, override_settings


@override_settings(ALLOWED_HOSTS=["testserver", "rasova.net", ".rasova.net"],
                   CANONICAL_HOST="rasova.net")
class SearchIndexingTest(TestCase):

    def get(self, path, host):
        return self.client.get(path, HTTP_HOST=host)

    def test_marketing_pages_on_the_main_site_can_be_indexed(self):
        for path in ("/", "/compare/"):
            response = self.get(path, "rasova.net")
            self.assertEqual(response.status_code, 200, path)
            self.assertNotIn("X-Robots-Tag", response, path)

    def test_login_page_is_kept_out_of_search(self):
        response = self.get("/login/", "rasova.net")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_compare_on_a_subdomain_moves_to_the_main_site(self):
        for host in ("spice.rasova.net", "nosuchtenant.rasova.net"):
            response = self.get("/compare/", host)
            self.assertEqual(response.status_code, 301, host)
            self.assertEqual(response["Location"], "https://rasova.net/compare/")

    def test_www_moves_to_the_main_site(self):
        response = self.get("/compare/?ref=x", "www.rasova.net")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "https://rasova.net/compare/?ref=x")

    def test_home_page_copy_on_a_subdomain_is_kept_out_of_search(self):
        # Staff still open their own subdomain; it just is not a search result.
        response = self.get("/", "spice.rasova.net")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_login_on_a_subdomain_is_kept_out_of_search(self):
        response = self.get("/login/", "spice.rasova.net")
        self.assertEqual(response["X-Robots-Tag"], "noindex")

    def test_robots_and_sitemap_are_left_alone(self):
        for path in ("/robots.txt", "/sitemap.xml"):
            response = self.get(path, "rasova.net")
            self.assertEqual(response.status_code, 200, path)
            self.assertNotIn("X-Robots-Tag", response, path)

    def test_form_posts_on_a_subdomain_are_not_redirected(self):
        # A redirect would drop the posted form, so only GET and HEAD move.
        response = self.client.post("/compare/", HTTP_HOST="www.rasova.net")
        self.assertNotEqual(response.status_code, 301)


class GuestMenuIndexingTest(TestCase):
    """The guest menu on a restaurant's own subdomain stays indexable."""

    def test_guest_menu_paths_are_indexable_on_a_subdomain(self):
        from core.middleware import SearchIndexingMiddleware
        for path in ("/menu/digital-menu/", "/menu/qr/"):
            self.assertTrue(SearchIndexingMiddleware.indexable("spice.rasova.net", path), path)
        self.assertFalse(SearchIndexingMiddleware.indexable("spice.rasova.net", "/menu/"))
        self.assertFalse(SearchIndexingMiddleware.indexable("rasova.net", "/menu/digital-menu/"))
