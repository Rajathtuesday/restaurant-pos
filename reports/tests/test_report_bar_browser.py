"""
The one report bar on every report page (reports/_report_subnav.html,
15 Nov 2026), in a real browser.

Why: each report page carried its own copy of the bar. Kitchen KPIs had
drifted: its bar sat boxed inside the page, with other dropdowns, and its
Print and a back arrow were in the header; four pages had no Custom Range;
and the filters were sent by inline onchange handlers. Every page now
shows the same bar, straight under the header, at full width.
The Kitchen KPIs check fails on the old page.
Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test reports.tests.test_report_bar_browser
"""
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant

PAGES = ["/reports/dashboard/", "/reports/kitchen/", "/reports/inventory/", "/reports/menu-engineering/",
         "/reports/labor/", "/reports/audit/", "/reports/crm-analytics/"]

BAR = r"""
const main = document.querySelector("main");
const bar = document.querySelector("nav.report-subnav");
if (!bar) return {bar: false};
const period = bar.querySelector('select[name="date_filter"]');
return {
  bar: true,
  underHeader: bar.parentElement === main,
  fullWidth: Math.round(bar.getBoundingClientRect().width) === main.clientWidth,
  tabs: bar.querySelectorAll(".report-tab").length,
  custom: !!(period && period.querySelector('option[value="custom"]')),
  print: !!bar.querySelector('[data-action="page.print"]'),
  headerBack: !!document.querySelector('header .bi-arrow-left'),
};
"""


class ReportBarBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "bar-alpha")

    def test_every_report_page_has_the_same_bar(self):
        found = {}
        for url in PAGES:
            out = self.run_page(url, [("bar", BAR)], user=self.r.owner)
            self.assertEqual(out["errors"], [], f"{url}: " + "\n".join(out["errors"]))
            found[url] = out["results"]["bar"]
        expected = {"bar": True, "underHeader": True, "fullWidth": True, "tabs": 8, "custom": True,
                    "print": True, "headerBack": False}
        wrong = {url: got for url, got in found.items() if got != expected}
        self.assertEqual(wrong, {}, "these report pages differ from the one bar")

    def test_custom_range_opens_the_dates_and_another_period_sends_the_form(self):
        out = self.run_page("/reports/kitchen/", [
            ("hidden", 'return document.getElementById("customDates").offsetParent === null;'),
            ("opened", 'const s = document.querySelector(\'select[name="date_filter"]\'); s.value = "custom";'
                       's.dispatchEvent(new Event("change", {bubbles: true}));'
                       'return document.getElementById("customDates").offsetParent !== null;'),
            ("sent", 'const s = document.querySelector(\'select[name="date_filter"]\'); s.value = "weekly";'
                     's.dispatchEvent(new Event("change", {bubbles: true})); return true;', {"selector": "nav.report-subnav"}),
            ("url", "return location.search;"),
        ], user=self.r.owner)
        self.assertEqual(out["errors"], [])
        self.assertTrue(out["results"]["hidden"])
        self.assertTrue(out["results"]["opened"])
        self.assertIn("date_filter=weekly", out["results"]["url"])
