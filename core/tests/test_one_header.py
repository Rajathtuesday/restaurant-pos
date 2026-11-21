"""
One header for every page (V2, 13 November 2026).

Why: 56 pages wrote their own header. 32 had their own theme button (by
onclick), about 22 had none, most had no logout, and the name and
subtitle were written a dozen ways. base.html now draws the header and a
page fills only its slots: page_title, page_outlet, header_links,
header_badges and header_theme.

The trap this guards: Django ignores a child template's block when the
parent has no block of that name. A page still overriding the old
header_left or header_right would lose its buttons with no error at all.

What a page looks like in a browser (one brand, one theme switch, one
logout, none of them by onclick) is checked on every owner page by the
smoke walk (core/tests/test_browser_smoke_walk.py).

Run: python manage.py test core.tests.test_one_header
"""
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase

from core.tests.test_tenant_walk import Restaurant

ROOT = Path(settings.BASE_DIR)
SLOT = re.compile(r"{%\s*block (page_title|header_links|page_outlet)\s*%}(.*?){%\s*endblock", re.S)


def templates():
    out = subprocess.run(["git", "ls-files", "*.html"], capture_output=True, text=True, cwd=ROOT).stdout.split()
    return [p for p in out if "/templates/" in "/" + p and not p.startswith("md_files/")]


class HeaderSlotsTest(TestCase):

    def test_no_page_uses_the_old_header_blocks(self):
        old = re.compile(r"{%\s*block header_(left|right)\s*%}")
        using = [p for p in templates() if old.search((ROOT / p).read_text(encoding="utf-8"))]
        self.assertEqual(using, [], "these pages override a block base.html no longer has; their header is ignored")

    def test_no_page_repeats_what_the_header_already_has(self):
        repeats = []
        for path in templates():
            for name, body in SLOT.findall((ROOT / path).read_text(encoding="utf-8")):
                for what, mark in (("a theme switch", "theme"), ("a logout link", "/logout/"),
                                   ("the alert badges", "_notif_badges"), ("an onclick theme", "themeManager")):
                    if mark == "theme" and not re.search(r"data-action=\"theme\.toggle\"|themeManager\.toggle", body):
                        continue
                    if mark != "theme" and mark not in body:
                        continue
                    repeats.append(f"{path} {name}: {what}")
        self.assertEqual(repeats, [])

    def test_titles_have_no_em_dash(self):
        dash = chr(0x2014)
        found = [f"{p} {name}" for p in templates()
                 for name, body in SLOT.findall((ROOT / p).read_text(encoding="utf-8")) if dash in body]
        self.assertEqual(found, [])


class HeaderRenderedTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.r = Restaurant("Alpha", "header-alpha")

    def page(self, url):
        client = Client()
        client.force_login(self.r.owner)
        response = client.get(url)
        self.assertEqual(response.status_code, 200, url)
        html = response.content.decode()
        header = html[html.index('<header class="pos-header">'):html.index("</header>")]
        return header

    def test_the_subtitle_is_the_page_then_the_outlet(self):
        header = self.page("/kitchen/")
        self.assertIn('<span class="page-name">Kitchen Display</span><span class="outlet-name">Alpha Outlet</span>', header)

    def test_a_breadcrumb_title_links_to_its_parent(self):
        header = self.page("/inventory/variance/")
        self.assertIn('<a href="/inventory/board/">Inventory</a> / Variance', header)

    def test_no_template_comment_shows_on_the_page(self):
        """A {# #} comment may not span lines: Django prints it as text."""
        client = Client()
        client.force_login(self.r.owner)
        html = client.get("/billing/").content.decode()
        self.assertNotIn("{#", html)
        self.assertNotIn("One header for every page", html)

    def test_every_part_is_there_once(self):
        for url in ("/billing/", "/tables/", "/reports/dashboard/", "/setup/", "/kitchen/", "/token/"):
            header = self.page(url)
            self.assertEqual(header.count('href="/logout/"'), 1, url)
            self.assertEqual(header.count('id="themeIcon"'), 1, url)
            self.assertEqual(header.count('class="brand-title"'), 1, url)
            self.assertNotIn("themeManager", header, url)

    def test_the_kitchen_keeps_its_own_switch(self):
        header = self.page("/kitchen/")
        self.assertIn('data-action="kitchen.light"', header)
        self.assertNotIn('data-action="theme.toggle"', header)

    def test_billing_keeps_its_wide_alert_badges(self):
        # A worded control the header fits (static/js/core/header_fit.js): its
        # word in its own span, so it can fold to the icon on a narrow screen.
        header = self.page("/billing/")
        self.assertIn('data-fit="Calls"', header)
        self.assertIn('class="btn-luxury text-decoration-none" title="Calls" aria-label="Calls">'
                      '<i class="bi bi-bell"></i><span class="fit-word">Calls</span>', header)
