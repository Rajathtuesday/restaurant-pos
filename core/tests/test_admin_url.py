"""
The Django admin at its own unguessable address (ADMIN_URL), and only there.

Why (27 Nov 2026): /admin/ is the first address every scanner tries, and
production is meant to set ADMIN_URL to something else (core/settings.py).
But six places wrote "/admin/" by hand, so with the setting changed their
links went nowhere; the reports dashboard showed its "System Admin" link to
every owner and manager, which would have printed the secret address into
every restaurant's page; the service worker kept every page it saw for
offline, admin pages included, under any address but /admin/; and the
admin's login had the lockout but not the login page's rate limit.

Now: every admin link is reversed from the admin's own URLs and drawn only
for superusers; an admin response says it is not for offline
(X-Rasova-Offline: never), so the service worker needs no address; the
admin login shares the staff login's rate limits.

Run: python manage.py test core.tests.test_admin_url
"""
import importlib

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import clear_url_caches

from accounts.dashboard_tiles import dashboard_tiles
from accounts.models import User
from core.testing import freeze_ratelimit_clock
from tenants.models import Outlet, Tenant

SECRET = "back-office-x9/"
TOO_FAST = "Too many tries from this network. Wait a minute."


def reload_urls():
    import core.urls
    importlib.reload(core.urls)
    clear_url_caches()


class _AtASecretPath(TestCase):
    """ADMIN_URL set as production sets it; the URL map rebuilt for it."""

    def setUp(self):
        override = override_settings(ADMIN_URL=SECRET)
        override.enable()

        def restore():
            override.disable()
            reload_urls()
        self.addCleanup(restore)
        reload_urls()
        self.tenant = Tenant.objects.create(name="Admin Pub", slug="admin-pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(username="admin_pub_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.root = User.objects.create_superuser(username="rasova_root", password="pw", email="", role="owner")

    def as_(self, user):
        client = Client()
        client.force_login(user)
        return client


class AdminAddressTest(_AtASecretPath):

    def test_the_admin_answers_only_at_its_own_address(self):
        self.assertEqual(Client().get("/admin/").status_code, 404)
        resp = Client().get("/" + SECRET)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/" + SECRET + "login/", resp["Location"])

    def test_every_admin_link_a_superuser_sees_goes_to_it(self):
        pages = {
            "/superuser/": "/" + SECRET,
            f"/superuser/tenant/{self.tenant.id}/": f"/{SECRET}tenants/tenant/{self.tenant.id}/change/",
            f"/reports/dashboard/?tenant_id={self.tenant.id}": "/" + SECRET,
        }
        root = self.as_(self.root)
        for url, link in pages.items():
            html = root.get(url).content.decode()
            self.assertNotIn('href="/admin/', html, url)
            self.assertIn(f'href="{link}"', html, url)
        tiles = [t.href for group in dashboard_tiles(self.root, set()) for t in group.tiles]
        self.assertIn("/" + SECRET, tiles)
        self.assertNotIn("/admin/", tiles)

    def test_an_owner_never_sees_the_address(self):
        owner = self.as_(self.owner)
        for url in ("/reports/dashboard/", "/dashboard/"):
            html = owner.get(url).content.decode()
            self.assertNotIn(SECRET, html, url)
            self.assertNotIn('href="/admin/', html, url)

    def test_robots_txt_never_names_it(self):
        self.assertNotIn(SECRET, Client().get("/robots.txt").content.decode())

    def test_admin_pages_say_they_are_not_for_offline_and_app_pages_do_not(self):
        self.assertEqual(Client().get(f"/{SECRET}login/")["X-Rasova-Offline"], "never")
        self.assertEqual(self.as_(self.root).get("/" + SECRET)["X-Rasova-Offline"], "never")
        self.assertNotIn("X-Rasova-Offline", self.as_(self.owner).get("/reports/dashboard/"))


@override_settings(RATELIMIT_ENABLE=True)
class AdminLoginRateLimitTest(_AtASecretPath):
    """The same limits as the staff login (accounts/views/auth_views.py)."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        freeze_ratelimit_clock(self)

    def post(self, path, username, address="203.0.113.7"):
        return Client(REMOTE_ADDR=address).post(path, {"username": username, "password": "guess"})

    def test_eleven_tries_for_one_name_in_a_minute_are_slowed(self):
        for _ in range(10):
            self.assertNotEqual(self.post(f"/{SECRET}login/", "rasova_root").status_code, 429)
        resp = self.post(f"/{SECRET}login/", "rasova_root")
        self.assertEqual(resp.status_code, 429)
        self.assertContains(resp, TOO_FAST, status_code=429)

    def test_it_shares_one_address_budget_with_the_staff_login(self):
        for n in range(60):
            self.post("/login/", f"nobody_{n}")
        self.assertEqual(self.post(f"/{SECRET}login/", "rasova_root").status_code, 429)
        self.assertNotEqual(self.post(f"/{SECRET}login/", "rasova_root", address="192.0.2.20").status_code, 429)
