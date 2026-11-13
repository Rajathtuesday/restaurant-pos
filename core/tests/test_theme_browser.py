"""
The page behind every screen follows the theme, in a real browser.

Why, in the order it was found (12 and 13 November 2026):

1. Phase 0 of the frontend rebuild moved base.html's inline styles into
   static/css/rasova.css. One of them, a black background on <html> and
   <body> to avoid a white flash before the stylesheets load, used to sit
   before the theme's stylesheet, where the theme's own body background
   replaced it. In rasova.css it came after the theme and won: in light
   mode every page was white cards on a black page.

2. Taking that rule out fixed a fresh page load and nothing else. <html>
   itself was still hard-coded black in the markup, in both modes, so
   anything <body> did not cover showed black. And the service worker kept
   serving its saved copy of the old stylesheet on every normal page load:
   only a hard refresh showed the fix. Going to another screen and back
   brought the black page back.

So this checks the root, not one load: the colour of <html> and of <body>,
in light and in dark, after going to another page and coming back, and
that the service worker hands out the stylesheet the server has now, not
one it saved earlier.

Run: python manage.py test core.tests.test_theme_browser
"""
from accounts.models import User
from core.tests.browser import BrowserTestCase
from tenants.models import Outlet, Tenant

LIGHT, DARK = "rgb(250, 250, 250)", "rgb(0, 0, 0)"      # --bg-color in static/css/themes/luxury.css

# The theme fades the page's colour over 0.3 s: wait until it stops moving.
SETTLED = """
async function settled() {
  let last = null;
  await t.until(async () => {
    const now = getComputedStyle(document.body).backgroundColor + getComputedStyle(document.documentElement).backgroundColor;
    const still = now === last;
    last = now;
    if (!still) await new Promise((r) => setTimeout(r, 150));
    return still;
  });
}
"""
# What is painted behind everything, and what the browser was told.
LOOK = SETTLED + """
await settled();
const meta = (name) => document.querySelector('meta[name="' + name + '"]').content;
return {
  dark: document.documentElement.classList.contains("dark"),
  html: getComputedStyle(document.documentElement).backgroundColor,
  body: getComputedStyle(document.body).backgroundColor,
  text: getComputedStyle(document.body).color,
  fixed: document.documentElement.style.background + document.documentElement.style.backgroundColor,
  scheme: meta("color-scheme"),
  bar: meta("theme-color"),
  path: location.pathname,
};
"""
TOGGLE = """
t.click("button:has(#themeIcon)");
await new Promise((r) => setTimeout(r, 50));
"""


class ThemeBackgroundTest(BrowserTestCase):

    def setUp(self):
        tenant = Tenant.objects.create(name="Theme Test")
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        self.owner = User.objects.create_user(username="theme_owner", password="x", role="owner",
                                              tenant=tenant, outlet=outlet)

    def look(self, steps):
        out = self.run_page("/shifts/", steps, user=self.owner)
        self.assertEqual(out["errors"], [])
        return out["results"]

    def assertPage(self, seen, colour, scheme, where):
        self.assertEqual((seen["html"], seen["body"]), (colour, colour), f"{where}: the page is not the theme's colour")
        self.assertEqual(seen["fixed"], "", f"{where}: a colour is fixed on <html> in the markup")
        self.assertEqual(seen["scheme"], scheme, where)
        self.assertNotEqual(seen["text"], seen["body"], f"{where}: text is the page's own colour")

    def test_light_mode_is_light_on_every_visit_and_dark_mode_is_dark(self):
        seen = self.look([
            ("light", LOOK),
            # to another screen, as a click does
            ("away", "location.href = '/setup/onboard/'; return true;", "/setup/onboard/"),
            ("other_screen", LOOK),
            # and back to the previous screen
            ("back", "history.back(); return true;", "/shifts/"),
            ("light_after_back", LOOK),
            ("dark", TOGGLE + LOOK),
            ("away_dark", "location.href = '/setup/onboard/'; return true;", "/setup/onboard/"),
            ("other_screen_dark", LOOK),
            ("back_dark", "history.back(); return true;", "/shifts/"),
            ("dark_after_back", LOOK),
            ("light_again", TOGGLE + LOOK),
        ])
        self.assertPage(seen["light"], LIGHT, "light", "first visit, light")
        self.assertPage(seen["other_screen"], LIGHT, "light", "another screen, light")
        self.assertEqual(seen["light_after_back"]["path"], "/shifts/")
        self.assertEqual(seen["other_screen"]["path"], "/setup/onboard/")
        self.assertPage(seen["light_after_back"], LIGHT, "light", "back to the previous screen, light")
        self.assertPage(seen["dark"], DARK, "dark", "after the toggle")
        self.assertEqual(seen["dark"]["bar"], "#000000")
        self.assertPage(seen["other_screen_dark"], DARK, "dark", "another screen, dark")
        self.assertPage(seen["dark_after_back"], DARK, "dark", "back to the previous screen, dark")
        self.assertPage(seen["light_again"], LIGHT, "light", "toggled back to light")
        self.assertEqual(seen["light_again"]["bar"], "#fafafa")

    def test_a_stylesheet_that_was_fixed_is_what_the_next_page_load_gets(self):
        """The service worker keeps static files so the app opens with no
        network. It used to answer from that copy first, always: a
        stylesheet fixed on the server went on arriving as it was before."""
        seen = self.look([
            ("stale", """
                await navigator.serviceWorker.ready;
                await t.until(() => navigator.serviceWorker.controller);
                const name = (await caches.keys()).find((key) => key.endsWith("-static"));
                const cache = await caches.open(name);
                const sheet = "/static/css/rasova.css";
                const forever = "/static/css/rasova.0123456789ab.css";
                // An old copy of each, as the cache would hold after the file changed on the server.
                await cache.put(sheet, new Response("html, body { background: #0a0a0a; }", { headers: { "Content-Type": "text/css" } }));
                await cache.put(forever, new Response("/* kept */", { headers: { "Content-Type": "text/css" } }));
                const now = await (await fetch(sheet)).text();
                const kept = await (await fetch(forever)).text();
                const refreshed = await (await cache.match(sheet)).text();
                return {
                  served_the_old_copy: now.includes("background: #0a0a0a"),
                  is_the_real_file: now.includes("Rasova's shared styles"),
                  cache_was_refreshed: refreshed.includes("Rasova's shared styles"),
                  hashed_name_from_cache: kept,
                };
            """),
        ])
        self.assertEqual(seen["stale"], {
            "served_the_old_copy": False,
            "is_the_real_file": True,
            "cache_was_refreshed": True,
            # a name that carries its content's hash never changes: the kept copy is right, and fast
            "hashed_name_from_cache": "/* kept */",
        })
