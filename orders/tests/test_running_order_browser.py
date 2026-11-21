"""
The running order page, in a real browser.

Why: the page asked "Why is this dish free?" with the browser's prompt(),
which never shows in the Android app, so Comp did nothing there; it hid
Comp from captains, whom the server lets make a dish free; and it showed a
failed refresh as nothing at all (console only). Since 14 Nov 2026 it is
static/js/running_order/ on base.html: Comp opens Rasova's own sheet, the
buttons follow the server, a failed refresh says so above the last list.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test orders.tests.test_running_order_browser
"""
from django.contrib.auth import get_user_model

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant

User = get_user_model()

LISTED = 'return await t.until(() => t.text("#items").includes("Alpha Paneer Tikka"));'


class RunningOrderBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "ro-browser")
        self.url = f"/order/{self.r.order.id}/"

    def assertNoErrors(self, out):
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))

    def unpaid(self):
        """The fixture's order carries a cash payment of 100. A free dish on a
        paid bill needs a manager to give the money back (the test below);
        this one is about the comp sheet, on a bill with nothing paid."""
        from payments.models import Refund
        Refund.objects.filter(order=self.r.order).delete()
        self.r.order.payments.all().delete()

    def test_a_captain_makes_a_dish_free_through_the_sheet(self):
        self.unpaid()
        captain = User.objects.create_user(username="ro-browser-captain", password="pw", role="captain",
                                           tenant=self.r.tenant, outlet=self.r.outlet)
        out = self.run_page(self.url, [
            ("listed", LISTED),
            ("before", 'return t.text("#total");'),
            ("open", 't.click("[data-action=\\"item.comp\\"]");'
                     'return await t.until(() => document.querySelector(".oe-popup .oe-chip"));'),
            ("reason", 't.click(".oe-popup .oe-chip[data-reason=\\"Birthday\\"]");'
                       't.click(".oe-popup .swal2-confirm"); return true;'),
            ("free", 'return await t.until(() => document.querySelector("#items .badge-comp"));'),
            ("after", 'return await t.until(() => t.text("#total") === "₹0" && t.text("#total"));'),
            ("comp_gone", 'return t.all("[data-action=\\"item.comp\\"]").length;'),
        ], user=captain)
        self.assertNoErrors(out)
        self.assertNotEqual(out["results"]["before"], "₹0")
        self.assertEqual(out["results"]["after"], "₹0")
        self.assertEqual(out["results"]["comp_gone"], 0)
        self.r.order_item.refresh_from_db()
        self.assertTrue(self.r.order_item.is_complimentary)

    def test_a_free_dish_on_a_paid_bill_needs_a_manager_to_give_back(self):
        """100 is paid in cash on a 525 bill; a free dish takes the bill to 0.
        The captain is asked for a manager's PIN, told what goes back; with it
        the dish is free and 100 is recorded as given back (21 Nov 2026,
        orders/services/paid_guard.py)."""
        from decimal import Decimal

        from orders.services.approvals import set_pin
        from payments.models import Refund
        Refund.objects.filter(order=self.r.order).delete()     # the fixture's pending request: not this test's
        captain = User.objects.create_user(username="ro-browser-captain2", password="pw", role="captain",
                                           tenant=self.r.tenant, outlet=self.r.outlet)
        manager = User.objects.create_user(username="ro-browser-manager", password="pw", role="manager",
                                           tenant=self.r.tenant, outlet=self.r.outlet, first_name="Meera")
        set_pin(manager, "7392")
        out = self.run_page(self.url, [
            ("listed", LISTED),
            ("open",'t.click("[data-action=\\"item.comp\\"]");'
                     'return await t.until(() => document.querySelector(".oe-popup .oe-chip"));'),
            ("reason", 't.click(".oe-popup .oe-chip[data-reason=\\"Birthday\\"]");'
                       't.click(".oe-popup .swal2-confirm"); return true;'),
            ("asked", 'return await t.until(() => document.getElementById("ra-pin")'
                      ' && document.querySelector(".swal2-popup").textContent);'),
            ("pin", 'document.getElementById("ra-pin").value = "7392";'
                    't.click(".swal2-confirm");'
                    'return await t.until(() => document.querySelector("#items .badge-comp"));'),
        ], user=captain)
        refused = [e for e in out["errors"] if "/complimentary-item/" in e and "403" in e]
        self.assertEqual([e for e in out["errors"] if e not in refused], [], "only the first ask is a 403")
        self.assertIn("gives back ₹100.00", out["results"]["asked"])
        given = Refund.objects.get(order=self.r.order, status="approved")
        self.assertEqual((given.amount, given.refunded_by), (Decimal("100.00"), manager))
        self.r.order.refresh_from_db()
        self.assertEqual(self.r.order.amount_paid(), self.r.order.grand_total)

    def test_a_waiter_sees_the_dishes_and_no_edit_buttons(self):
        out = self.run_page(self.url, [
            ("listed", LISTED),
            ("buttons", 'return t.all("[data-action]").map((b) => b.dataset.action)'
                        '.filter((a) => a.startsWith("item.") || a.startsWith("order."));'),
        ], user=self.r.staff)
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["buttons"], [])

    def test_a_failed_refresh_says_so_and_keeps_the_list(self):
        out = self.run_page(self.url, [
            ("listed", LISTED),
            ("down", 'window.fetch = () => Promise.reject(new TypeError("Failed to fetch"));'
                     'return await t.until(() => !document.getElementById("ro-status").hidden'
                     ' && t.text("#ro-status"));'),
            ("still", 'return t.text("#items").includes("Alpha Paneer Tikka");'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertIn("Check the connection", out["results"]["down"])
        self.assertTrue(out["results"]["still"])
