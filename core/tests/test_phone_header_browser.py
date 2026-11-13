"""
The header on a phone is the name and one row of controls (13 Nov 2026).

Why: at 390px the header's controls wrapped into rows of icons: the floor
plan's header was 321px tall (4 rows) on an 844px phone, the token counter's
274px, billing's 172px. Now they form one row that scrolls sideways, and
the page itself never scrolls sideways.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test core.tests.test_phone_header_browser
"""
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant

PHONE = {"width": 390, "height": 844, "mobile": True}
MEASURE = """
await t.until(() => document.readyState === "complete");
await new Promise((r) => setTimeout(r, 400));
const header = document.querySelector("header.pos-header");
const right = header.querySelector(".header-right");
const controls = Array.from(right.querySelectorAll("a, button")).filter((el) => el.offsetParent !== null);
// A row is controls whose middles are within 16px of each other (a 36px chip
// and a 44px icon on one line have different tops, the same middle).
const middles = controls.map((el) => { const b = el.getBoundingClientRect(); return b.top + b.height / 2; })
  .sort((a, b) => a - b);
const rows = middles.filter((m, i) => i === 0 || m - middles[i - 1] > 16).length;
return { height: Math.round(header.getBoundingClientRect().height), rows, controls: controls.length,
         pageScrollsSideways: document.documentElement.scrollWidth > window.innerWidth + 1 };
"""


class PhoneHeaderTest(BrowserTestCase):

    def test_one_row_of_controls_on_every_working_screen(self):
        r = Restaurant("Alpha", "phone-alpha")
        seen = {}
        for url in ("/billing/", "/tables/", "/kitchen/", "/dashboard/", f"/token/{r.order.id}/bill/"):
            out = self.run_page(url, [("m", MEASURE)], user=r.owner, viewport=PHONE)
            self.assertEqual(out["errors"], [], f"{url}: {out['errors']}")
            seen[url] = out["results"]["m"]
        for url, m in seen.items():
            self.assertEqual(m["rows"], 1, f"{url}: {m}")
            # 13 Nov 2026, after: billing 123px (was 172), floor plan 153px (was 321).
            self.assertLessEqual(m["height"], 160, f"{url}: {m}")
            self.assertGreater(m["controls"], 2, f"{url}: {m}")
            self.assertFalse(m["pageScrollsSideways"], f"{url}: {m}")
