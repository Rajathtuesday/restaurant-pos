"""
The browser smoke walk: every page the owner of a fully set-up fine-dining
restaurant can open, opened in a real browser.

Two breaks sat in unpushed commits for a day in November 2026 because no
test opened the pages they broke: a fonts <link> that lost its ">" made
the QR menu print its stylesheet as text, and the running order page
called a function at the top level that read a module's global before the
module had run. Rendering a template cannot see either; a browser sees
both at once.

The URL list is the tenant walk's (core/tests/test_tenant_walk.py): every
named route, its ids filled from the restaurant's own rows. A route is a
page when it answers the owner's GET with 200 and HTML. Each page fails on:

  - an uncaught error, or a console.error (how Chromium reports a failed request)
  - a static file that did not load
  - stylesheet text among the words on the page
  - a header that is not the one header (base.html): exactly one brand
    link, one theme switch, one logout, none by onclick (V2, 13 Nov 2026)

A new page is walked the day its URL is added, with no change here.
"""
import re
from urllib.parse import urlparse

from django.conf import settings
from django.test import Client
from django.urls import get_resolver, reverse

from core.testing import freeze_ratelimit_clock
from core.tests.browser import BrowserTestCase, _console_error, _wait_until_the_server_is_idle
from core.tests.test_tenant_walk import REGISTRY, Restaurant, _routes

# Routes that are not opened, each with its reason.
NOT_WALKED = {
    "logout": "ends the session the walk runs in",
    "live-demo": "logs in as the demo restaurant, replacing the owner",
    "demo_switch": "switches the session to demo mode",
}

# A CSS rule among the page's visible words: "selector { property: value }".
# innerText leaves out <style> and hidden elements, so a match means the
# browser is showing a stylesheet as text. JSON does not match: its keys
# are quoted.
CSS_AS_TEXT = r"""
const text = document.body ? document.body.innerText : "";
const found = text.match(/[.#@:\w\]\)*][^{}\n]{0,80}\{\s*[a-z-]+\s*:\s*[^{}"]{1,200}\}/);
return found ? found[0].slice(0, 120) : null;
"""

# The one header (templates/core/base.html): what every page that has the
# app's header must show once, and only once. A page that is not an app
# page (login, a receipt) has no header and is not asked.
HEADER = r"""
const header = document.querySelector("header.pos-header");
if (!header) return [];
const wrong = [];
const once = (what, selector) => {
  const n = header.querySelectorAll(selector).length;
  if (n !== 1) wrong.push(`${n} ${what} in the header, expected 1`);
};
once("brand links", 'a[href="/dashboard/"].brand-title');
once("theme switches", "#themeIcon");
once("logout links", 'a[href="/logout/"]');
const icons = document.querySelectorAll("#themeIcon").length;
if (icons !== 1) wrong.push(`${icons} #themeIcon on the page, expected 1`);
// The shared parts never by onclick. A page's own header buttons still
// using onclick are counted by the ratchet (test_frontend_hygiene) instead.
const inline = header.querySelectorAll('[data-header][onclick], [onclick*="themeManager"]').length;
if (inline) wrong.push(`${inline} shared header part(s) by onclick`);
return wrong;
"""


def owner_pages(restaurant):
    """(name, url) for every route that answers the owner's GET with a 200
    HTML page, ids pointing at the restaurant's own rows."""
    client = Client(raise_request_exception=False)
    client.force_login(restaurant.owner)
    pages = []
    for namespace, name, route in _routes(get_resolver().url_patterns):
        if not name or name in NOT_WALKED:
            continue
        if namespace.startswith("admin") or route.startswith("admin/") or route.startswith("^media/"):
            continue
        spec = re.findall(r"<(?:\w+:)?(\w+)>", route)
        if any(k not in REGISTRY for k in spec):
            continue    # keyed by a guest's token; the tenant walk lists these as public
        try:
            url = reverse(f"{namespace}:{name}" if namespace else name,
                          kwargs={k: REGISTRY[k](restaurant, route) for k in spec})
        except Exception:  # noqa: BLE001 -- the tenant walk reports a route that cannot be reversed
            continue
        response = client.get(url, HTTP_ACCEPT="text/html")
        if (response.status_code == 200 and not response.streaming
                and response.get("Content-Type", "").startswith("text/html")):
            pages.append((name, url))
    return pages


class BrowserSmokeWalkTest(BrowserTestCase):

    def setUp(self):
        freeze_ratelimit_clock(self)
        self.restaurant = Restaurant("Alpha", "walk-alpha")

    def open(self, context, url):
        """Open one page; return what went wrong on it, as a list of lines."""
        problems = []
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.on("pageerror", lambda error: problems.append(f"uncaught: {error}"))
        page.on("console", lambda message: problems.append(_console_error(message))
                if message.type == "error" else None)
        page.on("response", lambda response: problems.append(
            f"static file {response.status}: {urlparse(response.url).path}")
            if response.status >= 400 and "/static/" in response.url else None)
        page.on("requestfailed", lambda request: problems.append(
            f"static file failed: {urlparse(request.url).path}")
            if "/static/" in request.url else None)
        try:
            page.goto(self.live_server_url + url, wait_until="load")
            # The first requests a page makes by itself (its poll, its list)
            # answer within this; a page that never goes quiet is not waited for.
            try:
                page.wait_for_load_state("networkidle", timeout=2_000)
            except Exception:  # noqa: BLE001 -- a polling or streaming page
                pass
            css = page.evaluate("async () => {" + CSS_AS_TEXT + "}")
            if css:
                problems.append(f"stylesheet shown as text: {css!r}")
            problems.extend(page.evaluate("async () => {" + HEADER + "}"))
        except Exception as e:  # noqa: BLE001 -- reported as this page's problem
            problems.append(f"could not open: {str(e).splitlines()[0]}")
        finally:
            page.close()
        return problems

    def test_every_page_the_owner_can_open_loads_clean(self):
        pages = owner_pages(self.restaurant)
        client = Client()
        client.force_login(self.restaurant.owner)
        context = self.browser.new_context(viewport={"width": 1400, "height": 900})
        findings = []
        try:
            context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                  "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                                  "url": self.live_server_url}])
            for name, url in pages:
                # The same problem twice on one page is one finding.
                for problem in dict.fromkeys(self.open(context, url)):
                    findings.append(f"{url} ({name}): {problem}")
        finally:
            context.close()
            _wait_until_the_server_is_idle()
        self.assertEqual(findings, [], "\n\nThe browser smoke walk found problems:\n  " + "\n  ".join(findings))
        # 67 on 13 Nov 2026. Far fewer means the URL map or the fixture moved.
        self.assertGreater(len(pages), 60, f"the walk opened too few pages: {[name for name, _ in pages]}")
