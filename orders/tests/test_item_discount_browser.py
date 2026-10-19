"""
A dish's own discount on the bill page, in a real browser (Playwright,
Chromium, live server).

Until 19 Oct 2026 it never applied: the page asked "Apply discount %?"
with nothing to type in, then opened two browser prompts (which the
Android app never shows), and posted to /orders/item-discount/<id>/, an
address that didn't exist; the 404 page wasn't JSON, so nothing was said
either. Now it is one form (percent and reason) posting to the address
from its URL name, and a failure is shown.

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

    def test_a_refusal_is_shown_not_swallowed(self):
        # A printed bill is locked against dish discounts (validate_order_editable):
        # the cashier must see why, not a button that does nothing.
        order, page = self.bill_page(status="billing")
        page.fill("#item-discount-pct", "10")
        page.fill("#item-discount-reason", "regular guest")
        page.click(".swal2-confirm")
        page.wait_for_selector(".swal2-toast:has-text('locked')")
        self.assertEqual(self.item(order).item_discount_pct, D("0.00"))
        self.assertEqual(self.errors, [])
