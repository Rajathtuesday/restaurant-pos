"""
A manager's PIN on a cashier's screen, in a real browser (Playwright,
Chromium, live server). See orders/services/approvals.py.

The cashier gives a dish discount on a printed bill. The server answers
"needs approval", the screen asks for a manager and their PIN
(static/js/approval.js) and sends the same change again with it. A wrong
PIN says so and asks again; backing out changes nothing.

Skipped when Playwright or its Chromium isn't installed (CI has neither).
Run: python manage.py test orders.tests.test_approval_pin_browser
"""
import os
from decimal import Decimal as D
from unittest import SkipTest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem
from orders.services.approvals import set_pin
from tenants.models import Outlet, Tenant


@override_settings(SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   SESSION_COOKIE_DOMAIN=None, CSRF_COOKIE_DOMAIN=None,
                   PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ApprovalPinInTheBrowserTest(StaticLiveServerTestCase):

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
        self.tenant = Tenant.objects.create(name="PIN Pub")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.cashier = User.objects.create_user(username="pin_cashier", password="pw", role="cashier",
                                                tenant=self.tenant, outlet=self.outlet)
        self.manager = User.objects.create_user(username="pin_manager", password="pw", role="manager",
                                                first_name="Asha", tenant=self.tenant, outlet=self.outlet)
        set_pin(self.manager, "2580")
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                            name="Chilli Chicken", price=D("400"), gst_percentage=D("0"))
        self.errors, self.native_dialogs = [], []

    def printed_bill_page(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open",
                                     source="dine_in", created_by=self.cashier)
        OrderItem.objects.create(order=order, menu_item=self.dish, quantity=1, price=D("400"),
                                 gst_percentage=D("0"), total_price=D("400"), status="sent")
        order.recalculate_totals()
        order.status = "billing"
        order.save()
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
        page.click("button[aria-label='Discount on Chilli Chicken']")
        page.wait_for_selector("#item-discount-pct")
        page.fill("#item-discount-pct", "25")
        page.fill("#item-discount-reason", "food was cold")
        page.click(".swal2-confirm")
        page.wait_for_selector("#ra-pin")
        return order, page

    def test_the_managers_pin_approves_it(self):
        order, page = self.printed_bill_page()
        self.assertIn("A manager's PIN is needed", page.locator(".swal2-html-container").inner_text())
        self.assertIn("Asha", page.locator("#ra-who").inner_text())
        page.fill("#ra-pin", "2580")
        with page.expect_navigation():
            page.click(".swal2-confirm")
        order.refresh_from_db()
        self.assertEqual(order.grand_total, D("300.00"))
        event = OrderEvent.objects.get(order=order, event_type="changed_after_bill")
        self.assertEqual((event.metadata["approved_by"], event.created_by), ("pin_manager", self.cashier))
        self.assertEqual((self.native_dialogs, self.errors), ([], []))

    def test_a_wrong_pin_says_so_and_backing_out_changes_nothing(self):
        order, page = self.printed_bill_page()
        page.fill("#ra-pin", "1111")
        page.click(".swal2-confirm")
        page.wait_for_selector(".ra-error")
        self.assertIn("Wrong PIN. 4 tries left.", page.locator(".ra-error").inner_text())
        page.click(".swal2-cancel")
        order.refresh_from_db()
        self.assertEqual(order.grand_total, D("400.00"))
        self.assertFalse(OrderEvent.objects.filter(order=order, event_type="changed_after_bill").exists())
        self.assertEqual((self.native_dialogs, self.errors), ([], []))
