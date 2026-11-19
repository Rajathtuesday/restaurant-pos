"""
A brand-new restaurant's first day: every list says what it is for and
offers the one button that fills it (V9, 13 Nov 2026).

Why: a survey of every owner page as a new restaurant (no tables, no
dishes, no staff, no orders) found pages that left a new owner stuck. The
billing screen showed an empty "Menu" with no hint where dishes come from,
the floor plan said "All clear" with no way to add a table, the menu page
showed only a GST note, the table QR page was completely blank, the cash
register showed a table header and nothing else, and the rota said "add
staff first" with no link. Setup in under 30 minutes is a deal blocker;
a product that explains itself is half of it.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test core.tests.test_empty_states
"""
import re

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import get_resolver, reverse

from accounts.models import User
from core.features import get_all_known_features
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import _routes
from setup.models import PaymentConfig
from tenants.models import Outlet, Tenant, TenantFeatureOverride

# Page, then what its empty state says, and where its button goes.
FIRST_DAY = [
    ("/billing/", "No dishes yet", "/menu/"),
    ("/tables/", "No tables yet", "/setup/tables/"),
    ("/menu/", "No dishes yet", "/setup/menu/"),
    ("/setup/qr-codes/", "No tables yet", "/setup/tables/"),
    ("/shifts/sessions/", "No register sessions yet", "#openSessionModal"),
    ("/shifts/schedule/", "No staff yet", "/setup/staff/"),
]

EMPTY = """
const box = await t.until(() => document.querySelector("[data-empty-state]"));
const link = box.querySelector("a[href]");
const button = box.querySelector("button[data-bs-target]");
return { title: box.querySelector("h3").textContent.trim(),
         goes: link ? new URL(link.href).pathname : button ? button.dataset.bsTarget : null,
         shown: box.offsetParent !== null };
"""

WORDS = "const m = document.querySelector('main') || document.body; return m.innerText.trim().length;"


def new_restaurant():
    """Signed up a minute ago: an outlet, every feature, an owner, nothing else."""
    tenant = Tenant.objects.create(name="New Place", slug="new-place", tenant_type="fine_dining")
    outlet = Outlet.objects.create(tenant=tenant, name="Main")
    PaymentConfig.objects.create(tenant=tenant, outlet=outlet, cash_enabled=True)
    for feature in get_all_known_features():
        TenantFeatureOverride.objects.create(tenant=tenant, feature=feature, enabled=True)
    return User.objects.create_user(username="new-owner", password="pw", role="owner", tenant=tenant, outlet=outlet)


class FirstDayTest(BrowserTestCase):

    def setUp(self):
        self.owner = new_restaurant()

    def test_each_first_day_page_says_what_it_is_for_and_how_to_fill_it(self):
        client = Client()
        client.force_login(self.owner)
        seen = {}
        for url, title, goes in FIRST_DAY:
            out = self.run_page(url, [("empty", EMPTY)], user=self.owner)
            self.assertEqual(out["errors"], [], f"{url}:\n" + "\n".join(out["errors"]))
            seen[url] = out["results"]["empty"]
            self.assertEqual(seen[url], {"title": title, "goes": goes, "shown": True}, url)
            if goes.startswith("/"):
                self.assertEqual(client.get(goes).status_code, 200, f"{url}'s button leads to {goes}, which does not open")

    def test_the_open_register_button_opens_the_sheet(self):
        out = self.run_page("/shifts/sessions/", [
            ("open", '''t.click("[data-empty-state] button");
                        return await t.until(() => document.getElementById("openSessionModal").classList.contains("show"));'''),
        ], user=self.owner)
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["results"]["open"])

    def test_no_owner_page_is_blank(self):
        """Every page without an id in its address, as the new restaurant:
        none may show an empty page (the table QR page was 0 characters)."""
        client = Client(raise_request_exception=False)
        client.force_login(self.owner)
        pages = []
        for namespace, name, route in _routes(get_resolver().url_patterns):
            if not name or "<" in route or route.startswith("admin") or name in ("logout", "live-demo", "demo_switch"):
                continue
            try:
                url = reverse(f"{namespace}:{name}" if namespace else name)
            except Exception:  # noqa: BLE001 -- the tenant walk reports these
                continue
            response = client.get(url, HTTP_ACCEPT="text/html")
            if response.status_code == 200 and response.get("Content-Type", "").startswith("text/html") \
                    and not response.streaming:
                pages.append(url)
        context = self.browser.new_context(viewport={"width": 1400, "height": 900})
        blank = []
        try:
            context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                  "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                                  "url": self.live_server_url}])
            for url in pages:
                page = context.new_page()
                try:
                    page.goto(self.live_server_url + url, wait_until="load")
                    if page.evaluate("() => {" + WORDS + "}") < 5:     # no words at all
                        blank.append(url)
                finally:
                    page.close()
        finally:
            context.close()
            self.wait_for_the_server()
        self.assertGreater(len(pages), 40)
        self.assertEqual(blank, [], "these pages show a new restaurant nothing at all")


class EmptyStateMarkupTest(SimpleTestCase):
    """The include itself, without a browser."""

    def test_the_include_escapes_and_has_one_button(self):
        from django.template.loader import render_to_string
        html = render_to_string("core/_empty_state.html", {
            "icon": "inbox", "title": "<b>None</b>", "text": "Nothing", "href": "/x/", "label": "Add",
        })
        self.assertIn("&lt;b&gt;None&lt;/b&gt;", html)
        self.assertEqual(len(re.findall(r"<a |<button ", html)), 1)
        self.assertIn("data-empty-state", html)
