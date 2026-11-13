"""
The reports dashboard draws its charts, in a real browser.

Why: phase 0 of the frontend plan made base.html's scripts ES modules,
which run only once the page is parsed. The dashboard's script read
themeManager (set by static/js/core/app.js) while the page was still
parsing: it threw "themeManager is not defined" and neither chart was
drawn. The page answered 200, so no test saw it; the browser smoke walk
(core/tests/test_browser_smoke_walk.py) found it on 13 November 2026.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test reports.tests.test_dashboard_browser
"""
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant


class ReportsDashboardBrowserTest(BrowserTestCase):

    def setUp(self):
        self.restaurant = Restaurant("Alpha", "reports-alpha")

    def test_the_sales_chart_is_drawn_with_no_error(self):
        out = self.run_page("/reports/dashboard/", [
            ("chart", 'return await t.until(() => Boolean(Chart.getChart("salesChart")));'),
        ], user=self.restaurant.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        self.assertTrue(out["results"]["chart"])
