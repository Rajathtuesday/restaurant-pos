"""
The small pages moved to modules on 14 Nov 2026, in a real browser.

Why each one:
- Setup, tables: renaming asked with the browser's prompt(), which never
  shows in the Android app, so a table could not be renamed there. It is
  Rasova's own sheet now (ui.ask).
- The control panel's restaurant page put the restaurant's name straight
  into its script: a name with a backtick broke the script, so no preset
  button worked. The name is data on the button now, and the question is
  asked in Rasova's own sheet (confirm() never shows in the app either).
- The printer page, the login page's theme switch and the cash register:
  their inline handlers became modules; these check they still work.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test core.tests.test_small_pages_browser
"""
from django.contrib.auth import get_user_model

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from orders.models import Table
from shifts.models import CashSession

User = get_user_model()


class SmallPagesBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "small-pages")

    def assertNoErrors(self, out):
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))

    def test_a_table_is_renamed_in_rasovas_own_sheet(self):
        out = self.run_page("/setup/tables/", [
            ("ask", f't.click("[data-action=\\"table.rename\\"][data-name=\\"{self.r.table.name}\\"]");'
                    'return await t.until(() => document.querySelector(".swal2-input"));'),
            ("save", 'const input = document.querySelector(".swal2-input"); input.value = "Patio";'
                     't.click(".swal2-confirm"); return true;', {"selector": ".table-chip[data-name=\"Patio\"]"}),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.r.table.refresh_from_db()
        self.assertEqual(self.r.table.name, "Patio")
        self.assertTrue(Table.objects.filter(name="Patio", outlet=self.r.outlet).exists())

    def test_a_preset_applies_for_a_restaurant_whose_name_has_a_backtick(self):
        self.r.tenant.name = "Bob`s ${bar}"
        self.r.tenant.save(update_fields=["name"])
        admin = User.objects.create_user(username="small-pages-admin", password="pw", is_superuser=True, is_staff=True)
        out = self.run_page(f"/superuser/tenant/{self.r.tenant.id}/", [
            ("ask", 't.click("[data-action=\\"preset.apply\\"]");'
                    'return await t.until(() => document.querySelector(".swal2-html-container")'
                    ' && t.text(".swal2-html-container"));'),
            ("apply", 't.click(".swal2-confirm");'
                      'return await t.until(() => document.querySelector(".swal2-toast")'
                      ' && t.text(".swal2-toast"));'),
        ], user=admin)
        self.assertNoErrors(out)
        self.assertIn("Bob`s ${bar}", out["results"]["ask"])
        self.assertIn("Applied", out["results"]["apply"])

    def test_the_printer_page_picks_a_width_opens_advanced_and_formats_the_mac(self):
        out = self.run_page("/setup/printer/", [
            ("width", 't.click("[data-action=\\"printer.width\\"][data-width=\\"58\\"]");'
                      'return [document.getElementById("h_width").value,'
                      ' t.all(".width-card.selected").map((c) => c.dataset.width)];'),
            ("advanced", 't.click("[data-action=\\"printer.advanced\\"]");'
                         'return [document.getElementById("advRow").classList.contains("show"),'
                         ' document.querySelector("[data-action=\\"printer.advanced\\"]").getAttribute("aria-expanded")];'),
            ("mac", 'const mac = document.getElementById("macInput"); mac.value = "0057271";'
                    'mac.dispatchEvent(new Event("input", { bubbles: true })); return mac.value;'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["width"], ["58", ["58"]])
        self.assertEqual(out["results"]["advanced"], [True, "true"])
        self.assertEqual(out["results"]["mac"], "00:57:27:1")

    def test_the_login_theme_switch_lasts_past_a_reload(self):
        out = self.run_page("/login/", [
            ("toggle", 'const was = document.documentElement.classList.contains("dark");'
                       't.click("[data-action=\\"theme.toggle\\"]");'
                       'return [was, document.documentElement.classList.contains("dark"), localStorage.getItem("dark")];'),
            ("reload", 'localStorage.setItem("dark", "true"); document.documentElement.classList.remove("dark");'
                       'location.reload(); return true;', {"selector": "html.dark"}),
            ("icon", 'return await t.until(() => document.getElementById("themeIcon").className);'),
        ])
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["toggle"], [False, True, "true"])
        self.assertEqual(out["results"]["icon"], "bi bi-sun")

    def test_the_cash_register_opens_once_from_a_double_tap(self):
        CashSession.objects.filter(outlet=self.r.outlet).delete()
        out = self.run_page("/shifts/sessions/", [
            ("open", 'document.getElementById("open-amt").value = "500";'
                     'const b = document.querySelector("[data-action=\\"session.submit\\"]"); b.click(); b.click();'
                     'return true;', {"selector": "[data-bs-target=\"#closeSessionModal\"]"}),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertEqual(CashSession.objects.filter(outlet=self.r.outlet).count(), 1)

    def test_the_reports_tab_row_centres_the_current_report_on_a_phone(self):
        out = self.run_page("/reports/labor/", [
            ("strip", 'const active = await t.until(() => document.querySelector(".report-tab.active"));'
                      'const strip = active.parentElement;'
                      'await t.until(() => strip.scrollLeft > 0);'
                      'const a = active.getBoundingClientRect(), s = strip.getBoundingClientRect();'
                      'return [strip.scrollWidth > strip.clientWidth, Math.abs((a.left + a.width / 2) - (s.left + s.width / 2)) < 40];'),
        ], user=self.r.owner, viewport={"width": 390, "height": 800, "mobile": True})
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["strip"], [True, True])
