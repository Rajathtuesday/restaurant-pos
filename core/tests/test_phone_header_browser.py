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
            # 14 Nov 2026: floor plan 170px, as the brand link and the printer chip
            # became 44px, a fingertip (V15, core/tests/test_touch.py).
            self.assertLessEqual(m["height"], 175, f"{url}: {m}")
            self.assertGreater(m["controls"], 2, f"{url}: {m}")
            self.assertFalse(m["pageScrollsSideways"], f"{url}: {m}")


# Every link and button the header shows: inside the header's own row, on
# the screen and clear of the logo, or folded under More and listed there.
# A control the page's CSS hides on this screen (d-none d-sm-inline-flex)
# is not shown, as the page meant.
REACHABLE = """
await t.until(() => document.querySelector("[data-header-fit][data-fit-ready]"));
await new Promise((r) => setTimeout(r, 300));
const row = document.querySelector("[data-header-fit]");
const box = row.getBoundingClientRect();
const W = document.documentElement.clientWidth;
const brand = document.querySelector(".header-left").getBoundingClientRect();
const more = row.querySelector(".fit-more");
const wrong = [];
const folded = [];
for (const el of row.querySelectorAll("a[href], button")) {
  if (more.contains(el)) continue;
  // The More menu lists a marked control by its data-fit name.
  const marked = el.closest("[data-fit]");
  const name = marked ? marked.dataset.fit
    : (el.textContent.replace(/\\s+/g, " ").trim() || el.getAttribute("aria-label") || el.getAttribute("title") || "(no name)");
  if (el.closest(".fit-folded")) { folded.push(name); continue; }
  if (el.offsetParent === null) continue;
  const r = el.getBoundingClientRect();
  // Over the logo: only where the two share a line (on a phone the logo has its own).
  const overLogo = r.left < brand.right - 1 && r.right > brand.left + 1 && r.top < brand.bottom - 1 && r.bottom > brand.top + 1;
  if (r.left < Math.max(0, box.left) - 1 || r.right > Math.min(W, box.right) + 1 || overLogo) {
    wrong.push(`${name} at ${Math.round(r.left)}..${Math.round(r.right)}`);
  }
}
more.querySelector(".fit-more-btn").click();
const listed = [...more.querySelectorAll(".fit-menu-item:not(.fit-style) span:not(.fit-menu-badge)")].map((s) => s.textContent.trim());
more.querySelector(".fit-more-btn").click();
return { wrong, missing: folded.filter((n) => !listed.includes(n)) };
"""

ADD_FROM_MORE = """
await t.until(() => document.querySelector("[data-header-fit][data-fit-ready]"));
const add = [...document.querySelectorAll("[data-header-fit] button")].find((b) => b.textContent.includes("Add Ingredient"));
if (add.closest(".fit-folded")) {
  document.querySelector(".fit-more-btn").click();
  [...document.querySelectorAll(".fit-menu-item")].find((b) => b.textContent.includes("Add Ingredient")).click();
} else {
  add.click();
}
await t.until(() => document.querySelector("#inventoryModal.show"));
return true;
"""

TABLET = {"width": 768, "height": 1024}


class HeaderNeverRunsOffTest(BrowserTestCase):
    """Why (26 Nov 2026, item 4 of the fault plan): only controls marked
    data-fit could fold under More. A page's own links kept their width, and
    the right-aligned row ran off its left end over the logo: on a tablet
    Inventory's Add Ingredient and Consumption could not be reached, and
    35 more pages had unmarked header links that could do the same."""

    def setUp(self):
        self.r = Restaurant("Alpha", "fit-alpha")

    def check(self, url, viewport):
        out = self.run_page(url, [("seen", REACHABLE)], user=self.r.owner, viewport=viewport)
        self.assertEqual(out["errors"], [], f"{url}: {out['errors']}")
        self.assertEqual(out["results"]["seen"], {"wrong": [], "missing": []}, f"{url} at {viewport['width']} px")

    def test_inventory_on_a_tablet(self):
        self.check("/inventory/board/", TABLET)

    def test_inventory_on_a_phone(self):
        self.check("/inventory/board/", PHONE)

    def test_inventory_on_a_small_laptop(self):
        self.check("/inventory/board/", {"width": 1024, "height": 768})

    def test_add_ingredient_opens_on_a_tablet(self):
        out = self.run_page("/inventory/board/", [("add", ADD_FROM_MORE)], user=self.r.owner, viewport=TABLET)
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["results"]["add"])

    def test_pages_with_their_own_header_links_on_a_tablet(self):
        for url in ("/crm/reservations/", "/crm/", "/shifts/", "/inventory/suppliers/",
                    "/inventory/purchase-orders/", "/reports/dashboard/", "/menu/"):
            self.check(url, TABLET)
