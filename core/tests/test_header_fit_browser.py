"""
The header is one row that fits the screen, in Chromium (21 Nov 2026).

The floor plan's header was 13 controls and wrapped to three rows on a
tablet. static/js/core/header_fit.js now fits every control into one row:
names on the most important buttons as width allows, icons for the rest,
and what still does not fit under ... More, whose menu also holds each
person's choice of style (User.header_style).
Run: python manage.py test core.tests.test_header_fit_browser
"""
from accounts.models import User
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from tenants.models import TenantFeatureOverride

ROW = """
const row = document.querySelector("[data-header-fit]");
await t.until(() => row.hasAttribute("data-fit-ready"));
await new Promise((r) => setTimeout(r, 150));
const shown = [...row.querySelectorAll("[data-fit], .fit-more")].filter((el) => el.offsetParent !== null);
// One row: every control's middle on one line (a shorter chip sits lower at its top).
const middles = shown.map((el) => { const r = el.getBoundingClientRect(); return (r.top + r.bottom) / 2; });
const tops = new Set(middles.map((m) => Math.round((m - Math.min(...middles)) / 20)));
const named = shown.filter((el) => el.dataset.look === "both" || el.dataset.look === "word").map((el) => el.dataset.fit);
return { rows: tops.size, overflow: row.scrollWidth > row.clientWidth + 1, named,
         folded: [...row.querySelectorAll(".fit-folded")].map((el) => el.dataset.fit) };
"""


class HeaderFitBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "hdr-fit")
        for feature in ("floor_plan", "merge_tables", "waiter_call", "qr_menu"):
            TenantFeatureOverride.objects.update_or_create(tenant=self.r.tenant, feature=feature, defaults={"enabled": True})

    def header(self, width, user=None):
        out = self.run_page("/tables/", [("row", ROW)], user=user or self.r.owner, viewport={"width": width, "height": 900})
        self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
        return out["results"]["row"]

    def test_one_row_at_every_width(self):
        for width in (390, 768, 1024, 1366, 1920):
            with self.subTest(width=width):
                row = self.header(width)
                self.assertEqual(row["rows"], 1, row)
                self.assertFalse(row["overflow"], row)

    def test_names_from_tablet_size_most_important_first(self):
        self.assertIn("Tables", self.header(1024)["named"])
        wide = self.header(1920)["named"]
        for name in ("Tables", "Billing", "Combine", "Transfer"):
            self.assertIn(name, wide)

    def test_a_phone_folds_the_least_used_under_more(self):
        # With what room is left, the most important may keep its name.
        row = self.header(390)
        for rare in ("Logout", "Reports", "Kitchen"):
            self.assertIn(rare, row["folded"])
        for core in ("Tables", "Billing"):
            self.assertNotIn(core, row["folded"])

    def test_more_lists_what_folded_and_saves_the_persons_style(self):
        out = self.run_page("/tables/", [
            ("row", ROW),
            ("menu", 't.click(".fit-more-btn");'
                     'return t.all(".fit-menu .fit-menu-item").map((b) => b.textContent.trim());'),
            ("pick", 't.click(\'.fit-style[data-style="icons"]\');'
                     'await t.until(() => document.querySelector("[data-header-fit]").dataset.headerStyle === "icons");'
                     'await new Promise((r) => setTimeout(r, 300));'
                     'return [...document.querySelectorAll("[data-fit]")].filter((e) => e.offsetParent)'
                     '.every((e) => e.dataset.look === "icon");'),
        ], user=self.r.owner, viewport={"width": 768, "height": 900})
        self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
        menu = out["results"]["menu"]
        for folded in out["results"]["row"]["folded"]:
            self.assertIn(folded, " ".join(menu))
        for style in ("Fit the screen", "Icons and words", "Icons only", "Words only"):
            self.assertIn(style, menu)
        self.assertTrue(out["results"]["pick"], "every button an icon after picking Icons only")
        self.assertEqual(User.objects.get(pk=self.r.owner.pk).header_style, "icons", "saved on the account")

    def test_a_saved_style_is_used_on_the_next_page(self):
        User.objects.filter(pk=self.r.owner.pk).update(header_style="words")
        row = self.header(1920)
        self.assertGreater(len(row["named"]), 0)
        out = self.run_page("/tables/", [("icons", 'await t.until(() => document.querySelector("[data-fit-ready]"));'
                                                   'return [...document.querySelectorAll("[data-fit] .bi")]'
                                                   '.filter((i) => i.offsetParent).length;')],
                            user=self.r.owner, viewport={"width": 1920, "height": 900})
        self.assertEqual(out["results"]["icons"], 0, "words only: no icon shows")


class BusyScreensFitTest(BrowserTestCase):
    """The busy screens' own buttons fit too: billing, the bill, the running
    order, live orders and the token counter (21 Nov 2026)."""

    def setUp(self):
        from django.utils import timezone

        from core.utils import get_business_date
        from tokens.models import TokenOrder
        self.r = Restaurant("Alpha", "hdr-busy")
        for feature in ("floor_plan", "merge_tables", "waiter_call", "qr_menu", "kitchen_display", "reports",
                        "token_system"):
            TenantFeatureOverride.objects.update_or_create(tenant=self.r.tenant, feature=feature, defaults={"enabled": True})
        from orders.models import Order
        self.token_order = Order.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, created_by=self.r.owner,
                                                source="counter", status="open")
        TokenOrder.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, order=self.token_order, token_number=1,
                                  date=get_business_date(timezone.now(), self.r.outlet))

    def test_each_busy_screen_is_one_row_at_phone_tablet_and_laptop(self):
        pages = {
            "billing": "/billing/",
            "bill": f"/bill/{self.r.order.id}/",
            "running order": f"/order/{self.r.order.id}/",
            "live orders": "/live-orders/",
            "token counter": f"/token/{self.token_order.id}/bill/",
            # Group 2
            "kitchen": "/kitchen/",
            "waiter calls": "/waiter-dashboard/",
            "dashboard": "/dashboard/",
        }
        for label, url in pages.items():
            for width in (390, 768, 1366):
                with self.subTest(page=label, width=width):
                    out = self.run_page(url, [("row", ROW)], user=self.r.owner, viewport={"width": width, "height": 900})
                    self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
                    row = out["results"]["row"]
                    self.assertEqual(row["rows"], 1, (label, width, row))
                    self.assertFalse(row["overflow"], (label, width, row))


# The header's state while the fit has not run: whether its row shows, and
# how many rows its visible controls make.
BEFORE_FIT = """() => {
  const row = document.querySelector("[data-header-fit]");
  const shown = [...row.querySelectorAll("a, button")].filter((el) => el.offsetParent !== null);
  const middles = shown.map((el) => { const b = el.getBoundingClientRect(); return b.top + b.height / 2; }).sort((a, b) => a - b);
  return {
    fitted: row.hasAttribute("data-fit-ready"),
    visible: getComputedStyle(row).visibility === "visible",
    rows: middles.filter((m, i) => i === 0 || m - middles[i - 1] > 16).length,
  };
}"""


class HeaderBeforeTheFitTest(BrowserTestCase):
    """Why (26 Nov 2026, item 12a of the fault plan): the fit runs from
    core/app.js, a module that waits behind two large scripts, so every page
    load showed the header's full words, wrapped over two or three rows, then
    jumped to one fitted row. Until the fit runs, the row is one line and out
    of sight; if the script never runs, it shows anyway after two seconds.
    Here app.js never arrives, so the page is caught before its fit."""

    def setUp(self):
        self.r = Restaurant("Alpha", "flash-alpha")

    def page_without_app_js(self):
        from django.conf import settings
        from django.test import Client
        client = Client()
        client.force_login(self.r.owner)
        context = self.browser.new_context(viewport={"width": 1024, "height": 768})
        self.addCleanup(context.close)
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                              "value": client.cookies[settings.SESSION_COOKIE_NAME].value, "url": self.live_server_url}])
        context.route("**/js/core/app*.js", lambda route: route.abort())
        page = context.new_page()
        page.goto(self.live_server_url + "/tables/", wait_until="load")
        return page

    def test_no_wrapped_words_show_before_the_fit(self):
        page = self.page_without_app_js()
        page.wait_for_timeout(300)
        seen = page.evaluate(BEFORE_FIT)
        self.assertFalse(seen["fitted"])
        self.assertFalse(seen["visible"], seen)
        self.assertEqual(seen["rows"], 1, seen)

    def test_the_row_shows_anyway_if_the_script_never_runs(self):
        page = self.page_without_app_js()
        page.wait_for_timeout(2600)
        seen = page.evaluate(BEFORE_FIT)
        self.assertTrue(seen["visible"], seen)
        self.assertEqual(seen["rows"], 1, seen)
