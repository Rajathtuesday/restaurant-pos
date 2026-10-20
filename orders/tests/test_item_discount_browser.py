"""
A dish's own discount on the bill page, in a real browser (Playwright,
Chromium, live server).

Until 19 Oct 2026 it never applied: the page asked "Apply discount %?"
with nothing to type in, then opened two browser prompts (which the
Android app never shows), and posted to /orders/item-discount/<id>/, an
address that didn't exist; the 404 page wasn't JSON, so nothing was said
either. Now it is one form (percent and reason) posting to the address
from its URL name, and a failure is shown.

It also checks that every amount on the bill ends at the same right edge.

Skipped when Playwright or its Chromium isn't installed (CI has neither).
Run: python manage.py test orders.tests.test_item_discount_browser
"""
import os
from decimal import Decimal as D
from unittest import SkipTest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from tenants.models import Outlet, Tenant


@override_settings(SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   SESSION_COOKIE_DOMAIN=None, CSRF_COOKIE_DOMAIN=None)
class ItemDiscountInTheBrowserTest(StaticLiveServerTestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SkipTest("Playwright is not installed")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as e:
            cls.playwright.stop()
            super().tearDownClass()
            raise SkipTest(f"Chromium is not available: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Discount Bistro")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.cashier = User.objects.create_user(username="dd_cashier", password="pw", role="cashier",
                                                tenant=self.tenant, outlet=self.outlet)
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                            name="Paneer Butter Masala", price=D("300"), gst_percentage=D("0"))
        self.errors, self.native_dialogs = [], []

    def bill_page(self, status="open"):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open",
                                     source="dine_in", created_by=self.cashier)
        OrderItem.objects.create(order=order, menu_item=self.dish, quantity=1, price=D("300"),
                                 gst_percentage=D("0"), total_price=D("300"), status="sent")
        order.recalculate_totals()
        if status != "open":
            Order.objects.filter(pk=order.pk).update(status=status)
        client = Client()
        client.force_login(self.cashier)
        context = self.browser.new_context()
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                              "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                              "url": self.live_server_url}])
        page = context.new_page()
        page.set_default_timeout(15_000)
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("dialog", lambda d: (self.native_dialogs.append(d.message), d.dismiss()))
        page.goto(f"{self.live_server_url}/bill/{order.id}/")
        page.click("button[aria-label='Discount on Paneer Butter Masala']")
        page.wait_for_selector("#item-discount-pct")
        return order, page

    def item(self, order):
        return order.items.get()

    def test_the_form_applies_the_discount(self):
        order, page = self.bill_page()
        page.fill("#item-discount-pct", "10")
        page.fill("#item-discount-reason", "regular guest")
        with page.expect_navigation():
            page.click(".swal2-confirm")
        self.assertEqual(self.item(order).item_discount_pct, D("10.00"))
        order.refresh_from_db()
        self.assertEqual(order.grand_total, D("270.00"))
        self.assertIn("10% off", page.locator(".item-list").inner_text())
        self.assertEqual((self.native_dialogs, self.errors), ([], []))

    def test_a_discount_without_a_reason_is_stopped_in_the_form(self):
        order, page = self.bill_page()
        page.fill("#item-discount-pct", "10")
        page.click(".swal2-confirm")
        self.assertIn("Give a reason", page.locator(".swal2-validation-message").inner_text())
        self.assertEqual(self.item(order).item_discount_pct, D("0.00"))
        self.assertEqual((self.native_dialogs, self.errors), ([], []))

    def test_every_amount_lines_up_on_the_right(self):
        """19 Oct 2026: a dish's price and its offer line shared one
        left-aligned column, so a wide offer line ("happy 60 -108.00")
        pushed that dish's price left of every other amount. Now every
        amount on the bill ends at the same right edge."""
        order, page = self.bill_page()
        page.click(".swal2-cancel")
        lassi = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=self.dish.category,
                                        name="Cold Coffee", price=D("180"), gst_percentage=D("0"))
        OrderItem.objects.create(order=order, menu_item=lassi, quantity=1, price=D("180"),
                                 gst_percentage=D("0"), total_price=D("180"), status="sent")
        order.recalculate_totals()
        OrderItem.objects.filter(order=order, menu_item=lassi).update(
            offer_name="happy 60", offer_discount=D("108.00"))
        page.reload()
        page.wait_for_selector(".item-offer")
        # Where each rupee amount's text actually ends on screen, whatever
        # the markup: the dishes, the offer line, the money rows, the total.
        edges = page.evaluate("""() => {
            const out = [];
            for (const box of document.querySelectorAll('.item-list, .total-row, .grand-total')) {
                const walker = document.createTreeWalker(box, NodeFilter.SHOW_TEXT);
                for (let node = walker.nextNode(); node; node = walker.nextNode()) {
                    const match = node.textContent.match(/₹\s*[\d,]+\.\d\d/);
                    if (!match) continue;
                    const range = document.createRange();
                    const start = node.textContent.indexOf(match[0]);
                    range.setStart(node, start);
                    range.setEnd(node, start + match[0].length);
                    out.push(Math.round(range.getBoundingClientRect().right));
                }
            }
            return out;
        }""")
        self.assertGreaterEqual(len(edges), 5)            # 2 dishes, 1 offer, the money rows, the total
        self.assertLessEqual(max(edges) - min(edges), 1, edges)
        shots = os.environ.get("PROMO_E2E_SHOTS")
        if shots:
            page.locator(".item-list").screenshot(path=os.path.join(shots, "bill_lines_aligned.png"))
        self.assertEqual(self.errors, [])

    def test_a_refusal_is_shown_not_swallowed(self):
        # On a printed bill a cashier's dish discount needs a manager
        # (orders/services/approvals.py, since 20 Oct 2026; it used to be
        # refused for everyone). With no manager PIN set yet, the screen says
        # so, then shows the refusal: never a button that does nothing.
        order, page = self.bill_page(status="billing")
        page.fill("#item-discount-pct", "10")
        page.fill("#item-discount-reason", "regular guest")
        page.click(".swal2-confirm")
        page.wait_for_selector(".swal2-popup:has-text('No manager has set an approval PIN yet')")
        page.click(".swal2-confirm")
        page.wait_for_selector(".swal2-toast:has-text(\"manager's PIN\")")
        self.assertEqual(self.item(order).item_discount_pct, D("0.00"))
        self.assertEqual(self.errors, [])
