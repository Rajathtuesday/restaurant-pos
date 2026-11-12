"""
The page behind every screen follows the theme, in a real browser.

Why: phase 0 of the frontend rebuild (12 November 2026) moved base.html's
inline styles into static/css/rasova.css. One of them, a black background
on <html> and <body> to avoid a white flash before the stylesheets load,
used to sit before the theme's stylesheet, where the theme's own body
background replaced it. In rasova.css it came after the theme and won: in
light mode every page was white cards on a black page, with the section
headings (dark text) unreadable on it. Only the billing screen looked
right, because its panels paint their own backgrounds. No test looked at
a colour, so nothing failed.

Run: python manage.py test core.tests.test_theme_browser
"""
from accounts.models import User
from core.tests.browser import BrowserTestCase
from tenants.models import Outlet, Tenant

# The page's own colour, the colour of text written straight on it, and
# whether <body> covers the screen (<html> stays dark for the first paint).
SETTLED = """
// the theme fades the page's colour over 0.3 s: wait until it stops moving
async function settled() {
  let last = null;
  await t.until(async () => {
    const now = getComputedStyle(document.body).backgroundColor;
    const still = now === last;
    last = now;
    if (!still) await new Promise((r) => setTimeout(r, 150));
    return still;
  });
}
"""
LOOK = """
const body = getComputedStyle(document.body);
return {
  dark: document.documentElement.classList.contains("dark"),
  page: body.backgroundColor,
  text: body.color,
  covers: document.body.getBoundingClientRect().height >= window.innerHeight,
};
"""


class ThemeBackgroundTest(BrowserTestCase):

    def setUp(self):
        tenant = Tenant.objects.create(name="Theme Test")
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        self.owner = User.objects.create_user(username="theme_owner", password="x", role="owner",
                                              tenant=tenant, outlet=outlet)

    def test_the_page_is_light_in_light_mode_and_dark_in_dark_mode(self):
        out = self.run_page("/dashboard/", [
            ("light", LOOK),
            ("dark", SETTLED + """
                t.click("button:has(#themeIcon)");
                await t.until(() => document.documentElement.classList.contains("dark"));
                await settled();
            """ + LOOK),
            ("light_again", SETTLED + """
                t.click("button:has(#themeIcon)");
                await t.until(() => !document.documentElement.classList.contains("dark"));
                await settled();
            """ + LOOK),
        ], user=self.owner)
        self.assertEqual(out["errors"], [])
        seen = out["results"]
        # --bg-color and --text-main in static/css/themes/luxury.css
        light = {"dark": False, "page": "rgb(250, 250, 250)", "text": "rgb(26, 26, 26)", "covers": True}
        self.assertEqual(seen["light"]["page"], light["page"], "light mode: the page behind the cards is not light")
        self.assertEqual(seen["dark"]["page"], "rgb(0, 0, 0)")
        self.assertTrue(seen["dark"]["dark"] and seen["dark"]["covers"])
        self.assertEqual(seen["light_again"]["page"], light["page"])
        self.assertTrue(seen["light"]["covers"])
        # text on the page must not be the page's own colour, in either mode
        for mode in ("light", "dark"):
            self.assertNotEqual(seen[mode]["text"], seen[mode]["page"], mode)
