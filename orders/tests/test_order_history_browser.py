"""
The order history detail panel, in a real browser.

Why (14 Nov 2026, static/js/order_history/): the old inline script
- wrote the guest's name, dish names, refund reasons and audit lines into
  the panel as HTML;
- after a refund request, approval or rejection, read the panel heading
  back as the order id. The heading is the bill number ("INV-1-..."), so
  parseInt gave NaN and the panel never showed the change;
- approved with confirm() and rejected with prompt(), which never show in
  the Android app (Playwright, like the app, answers them with "no").

Each test below fails on the old page. Selectors are ones both pages share.
Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test orders.tests.test_order_history_browser
"""
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from orders.models import Order
from payments.models import Refund

OPEN_FIRST_ROW = (
    't.click("tbody tr");'
    'return await t.until(() => t.text("#detailBody").includes("Alpha Paneer Tikka"));'
)


class OrderHistoryBrowserTest(BrowserTestCase):

    def setUp(self):
        self.restaurant = Restaurant("Alpha", "hist-alpha")
        Order.objects.filter(pk=self.restaurant.order.pk).update(status="closed")

    def _errors(self, out):
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))

    def test_a_typed_name_shows_as_text(self):
        Order.objects.filter(pk=self.restaurant.order.pk).update(customer_name='<b id="injected">Ravi</b>')
        out = self.run_page("/history/", [
            ("open", OPEN_FIRST_ROW),
            ("injected", 'return document.querySelector("#detailBody #injected") !== null;'),
            ("text", 'return t.text("#detailBody").includes("<b id=");'),
        ], user=self.restaurant.owner)
        self._errors(out)
        self.assertFalse(out["results"]["injected"])
        self.assertTrue(out["results"]["text"])

    def test_a_refund_request_shows_in_the_panel(self):
        out = self.run_page("/history/", [
            ("open", OPEN_FIRST_ROW),
            ("asked", 't.click(".btn-refund");'
                      'document.getElementById("refundAmount").value = "20";'
                      'document.getElementById("refundReason").value = "Food was cold";'
                      't.click(".btn-refund-submit");'
                      'return await t.until(() => t.text("#detailBody").includes("Food was cold"));'),
        ], user=self.restaurant.owner)
        self._errors(out)
        self.assertTrue(Refund.objects.filter(order=self.restaurant.order, reason="Food was cold").exists())

    def test_reject_asks_for_the_reason_in_rasovas_sheet(self):
        out = self.run_page("/history/", [
            ("open", OPEN_FIRST_ROW),
            ("sheet", 't.click(".btn-reject"); return !!(await t.until(() => document.querySelector(".swal2-input")));'),
            ("rejected", 'document.querySelector(".swal2-input").value = "Guest was happy after all";'
                         't.click(".swal2-confirm");'
                         'return await t.until(() => t.text("#detailBody").includes("rejected"));'),
        ], user=self.restaurant.owner)
        self._errors(out)
        self.restaurant.refund.refresh_from_db()
        self.assertEqual(self.restaurant.refund.status, "rejected")

    def test_approve_asks_in_rasovas_sheet(self):
        out = self.run_page("/history/", [
            ("open", OPEN_FIRST_ROW),
            ("sheet", 't.click(".btn-approve"); return !!(await t.until(() => document.querySelector(".swal2-confirm")));'),
            ("approved", 't.click(".swal2-confirm");'
                         'return await t.until(() => t.text("#detailBody").includes("approved"));'),
        ], user=self.restaurant.owner)
        self._errors(out)
        self.restaurant.refund.refresh_from_db()
        self.assertEqual(self.restaurant.refund.status, "approved")
