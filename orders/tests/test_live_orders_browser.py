"""
The live orders screen lists the open orders, in a real browser.

Why: phase 1 of the frontend plan made order_edit.js a module, which runs
only once the page is parsed. The live orders script called load() while
the page was still parsing, and load() reads OrderEdit: it threw
"OrderEdit is not defined", the 8-second refresh on the next line never
started, and the screen stayed on "Loading" with no orders. The page
answered 200, so no test saw it; the browser smoke walk
(core/tests/test_browser_smoke_walk.py) found it on 13 November 2026.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test orders.tests.test_live_orders_browser
"""
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant


class LiveOrdersBrowserTest(BrowserTestCase):

    def setUp(self):
        self.restaurant = Restaurant("Alpha", "live-alpha")

    def test_the_open_order_is_listed_with_no_error(self):
        out = self.run_page("/live-orders/", [
            ("dish", 'return await t.until(() => t.text("#lo-grid").includes("Alpha Paneer Tikka"));'),
            ("open", 'return t.text("#st-open");'),
        ], user=self.restaurant.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        self.assertTrue(out["results"]["dish"])
        self.assertEqual(out["results"]["open"], "1")

    def test_a_failed_refresh_says_so_and_keeps_the_tickets(self):
        """Since 14 Nov 2026 (static/js/live_orders/): the board keeps what it
        drew and says the refresh failed, rather than going blank."""
        out = self.run_page("/live-orders/", [
            ("dish", 'return await t.until(() => t.text("#lo-grid").includes("Alpha Paneer Tikka"));'),
            ("down", 'window.fetch = () => Promise.reject(new TypeError("Failed to fetch"));'
                     'document.dispatchEvent(new Event("visibilitychange"));'
                     'return await t.until(() => !document.getElementById("lo-error").hidden && t.text("#lo-error"));'),
            ("kept", 'return t.text("#lo-grid").includes("Alpha Paneer Tikka");'),
        ], user=self.restaurant.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        self.assertIn("Check the connection", out["results"]["down"])
        self.assertTrue(out["results"]["kept"])

    def test_the_counter_filter_and_the_search(self):
        out = self.run_page("/live-orders/", [
            ("dish", 'return await t.until(() => t.text("#lo-grid").includes("Alpha Paneer Tikka"));'),
            ("counter", 't.click("[data-action=\\"lo.filter\\"][data-f=\\"online\\"]"); return t.text("#lo-grid");'),
            ("search", 't.click("[data-action=\\"lo.filter\\"][data-f=\\"all\\"]"); const q = document.getElementById("lo-q");'
                       'q.value = "nothing like this"; q.dispatchEvent(new Event("input", { bubbles: true })); return t.text("#lo-grid");'),
        ], user=self.restaurant.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        self.assertIn("Nothing matches", out["results"]["counter"])
        self.assertIn("Nothing matches", out["results"]["search"])
