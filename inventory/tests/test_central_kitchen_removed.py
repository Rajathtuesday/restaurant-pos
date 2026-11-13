"""
Central kitchen and stock requisitions were removed on 13 Nov 2026 (P2 in
the suggestions, release 1): built for chains that cook in one kitchen and
send to branches, while Rasova sells to single outlets. The code is kept
under the git tag archive/central-kitchen.

Release 1 removed the pages, addresses, views, tiles and the flags; the
models and their tables stay one more release, so the previous version can
still run against the database. Release 2 drops the tables, after a check
that no restaurant has rows (md_files/plan_p2_cut_apps_2026-11-13.html).

Run: python manage.py test inventory.tests.test_central_kitchen_removed
"""
from django.test import Client, TestCase

from core.features import get_all_known_features
from core.tests.test_tenant_walk import Restaurant

GONE = ["/inventory/central-kitchen/", "/inventory/central-kitchen/receive/", "/inventory/requisitions/",
        "/inventory/requisitions/1/", "/inventory/central-kitchen/batch/1/"]


class CentralKitchenRemovedTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.r = Restaurant("Alpha", "ck-gone")

    def test_its_addresses_are_gone(self):
        client = Client()
        client.force_login(self.r.owner)
        self.assertEqual({url: client.get(url).status_code for url in GONE}, {url: 404 for url in GONE})

    def test_its_flags_are_gone(self):
        known = set(get_all_known_features())
        self.assertNotIn("central_kitchen", known)
        self.assertNotIn("barcode_transfer", known)

    def test_no_screen_links_to_it(self):
        client = Client()
        client.force_login(self.r.owner)
        for url in ("/dashboard/", "/inventory/board/", "/setup/outlet/"):
            html = client.get(url).content.decode()
            self.assertNotIn("central-kitchen", html, url)
            self.assertNotIn("/inventory/requisitions/", html, url)
