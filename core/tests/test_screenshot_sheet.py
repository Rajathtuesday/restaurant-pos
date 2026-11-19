"""
The screenshot sheet: the key screens, light and dark, desktop and phone,
on one page, for a person to look at before a push.

Not a pixel test (those fail on every small change and get switched off).
A contact sheet: forty pictures you scroll through in a minute, so a
broken look is seen by you before a customer sees it. The light-mode bug
of 12 November (a black page behind white cards) passed every test and
would have been the first thing on this page.

Make one (about a minute; Playwright and Chromium, as the browser tests):

    SCREENSHOTS=1 python manage.py test core.tests.test_screenshot_sheet

It is written to md_files/screens/<date>_<time>/index.html (md_files is not
in git) and the path is printed. Without SCREENSHOTS=1 the sheet is
skipped, so the suite never spends a minute on pictures; the page-building
test below always runs.

The restaurant is the tenant walk's fully set-up fine-dining one, with an
open order, so every screen has something on it.
"""
import html
import os
from datetime import datetime
from pathlib import Path
from unittest import SkipTest

from django.conf import settings
from django.test import Client, SimpleTestCase

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant

SIZES = {"desktop": {"width": 1400, "height": 900}, "phone": {"width": 390, "height": 844}}
THEMES = ("light", "dark")
OUT = Path(settings.BASE_DIR) / "md_files" / "screens"


def screens(r):
    """(name, address, signed in) for each screen on the sheet."""
    return [
        ("Login", "/login/", False),
        ("Dashboard", "/dashboard/", True),
        ("Billing", "/billing/", True),
        ("Bill", f"/bill/{r.order.id}/", True),
        ("Floor plan", "/tables/", True),
        ("Kitchen", "/kitchen/", True),
        ("QR menu", f"/menu/digital-menu/?table_token={r.table.qr_token}", False),
        ("Token counter", f"/token/{r.order.id}/bill/", True),
        ("Reports", "/reports/dashboard/", True),
        ("Setup", "/setup/", True),
    ]


def sheet_html(when, shots):
    """The contact sheet. `shots` is [(screen, theme, size, file, problems)]:
    one row per screen, a picture for each theme and size, and under a
    picture whatever went wrong while it was taken."""
    rows = []
    by_screen = {}
    for screen, theme, size, file, problems in shots:
        by_screen.setdefault(screen, []).append((theme, size, file, problems))
    for screen, pictures in by_screen.items():
        cells = []
        for theme, size, file, problems in pictures:
            note = "".join(f'<p class="bad">{html.escape(p)}</p>' for p in problems)
            cells.append(f'<figure class="{size}"><a href="{html.escape(file)}"><img loading="lazy" '
                         f'src="{html.escape(file)}" alt="{html.escape(screen)}, {theme}, {size}"></a>'
                         f'<figcaption>{theme}, {size}</figcaption>{note}</figure>')
        rows.append(f'<section><h2>{html.escape(screen)}</h2><div class="row">{"".join(cells)}</div></section>')
    problems = sum(len(s[4]) for s in shots)
    summary = f"{len(shots)} pictures" + (f", <span class=\"bad\">{problems} problem(s)</span>" if problems else ", no errors")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rasova screens {html.escape(when)}</title>
<style>
body {{ font: 15px/1.5 system-ui, sans-serif; margin: 16px; background: #fff; color: #111; }}
h1 {{ font-size: 20px; }} h2 {{ font-size: 16px; margin: 24px 0 8px; }}
.row {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }}
figure {{ margin: 0; }} figure.desktop img {{ width: 420px; }} figure.phone img {{ width: 150px; }}
img {{ border: 1px solid #999; display: block; max-width: 100%; }}
figcaption {{ font-size: 13px; color: #555; }} .bad {{ color: #b00020; font-size: 13px; margin: 2px 0; }}
</style></head><body>
<h1>Rasova screens, {html.escape(when)}</h1>
<p>{summary}. Look at every picture in light and dark before you push. Click one to see it full size.</p>
{"".join(rows)}
</body></html>
"""


class SheetPageTest(SimpleTestCase):
    """The page itself, without a browser: always runs."""

    def test_one_row_per_screen_and_problems_under_their_picture(self):
        page = sheet_html("13 Nov 2026 21:00", [
            ("Billing", "light", "desktop", "billing_light_desktop.png", []),
            ("Billing", "dark", "phone", "billing_dark_phone.png", ["uncaught: boom <b>"]),
            ("Kitchen", "dark", "desktop", "kitchen_dark_desktop.png", []),
        ])
        self.assertEqual(page.count("<section>"), 2)
        self.assertEqual(page.count("<img "), 3)
        self.assertIn("uncaught: boom &lt;b&gt;", page)
        self.assertIn("1 problem(s)", page)
        self.assertNotIn("\u2014", page)   # no em dash

    def test_the_ten_screens(self):
        class Fake:
            order = type("O", (), {"id": 7})
            table = type("T", (), {"qr_token": "tok"})
        names = [s[0] for s in screens(Fake)]
        self.assertEqual(len(names), 10)
        self.assertEqual(len(set(names)), 10)


class ScreenshotSheet(BrowserTestCase):

    @classmethod
    def setUpClass(cls):
        if os.environ.get("SCREENSHOTS") != "1":
            raise SkipTest("set SCREENSHOTS=1 to make the screenshot sheet")
        super().setUpClass()

    def test_make_the_sheet(self):
        r = Restaurant("Alpha", "screens-alpha")
        when = datetime.now()
        folder = OUT / when.strftime("%Y-%m-%d_%H%M")
        folder.mkdir(parents=True, exist_ok=True)
        client = Client()
        client.force_login(r.owner)
        session = client.cookies[settings.SESSION_COOKIE_NAME].value
        shots = []
        for screen, url, signed_in in screens(r):
            for theme in THEMES:
                for size, viewport in SIZES.items():
                    file = f"{screen.lower().replace(' ', '_')}_{theme}_{size}.png"
                    problems = self.shoot(url, signed_in, session, theme, viewport, size == "phone", folder / file)
                    shots.append((screen, theme, size, file, problems))
        page = folder / "index.html"
        page.write_text(sheet_html(when.strftime("%d %b %Y %H:%M"), shots), encoding="utf-8")
        print(f"\nScreenshot sheet: {page}")
        self.assertEqual(len(shots), 40)

    def shoot(self, url, signed_in, session, theme, viewport, phone, path):
        context = self.browser.new_context(viewport=viewport, is_mobile=phone, has_touch=phone,
                                           service_workers="block")
        problems = []
        try:
            if signed_in:
                context.add_cookies([{"name": settings.SESSION_COOKIE_NAME, "value": session,
                                      "url": self.live_server_url}])
            dark = "true" if theme == "dark" else "false"
            context.add_init_script(f'try {{ localStorage.setItem("dark", "{dark}"); '
                                    f'localStorage.setItem("kitchen_light", "{"false" if theme == "dark" else "true"}"); }} catch (e) {{}}')
            page = context.new_page()
            page.on("pageerror", lambda e: problems.append(f"uncaught: {e}"))
            page.goto(self.live_server_url + url, wait_until="load")
            try:
                page.wait_for_load_state("networkidle", timeout=3_000)
            except Exception:  # noqa: BLE001 -- a page that polls never goes quiet
                pass
            page.wait_for_timeout(400)                    # the theme's 0.3 s fade
            page.screenshot(path=str(path), full_page=False)
        except Exception as e:  # noqa: BLE001 -- shown under the picture, the sheet goes on
            problems.append(f"could not take it: {str(e).splitlines()[0]}")
        finally:
            context.close()
            self.wait_for_the_server()
        return problems
