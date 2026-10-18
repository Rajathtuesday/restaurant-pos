"""
The promo fixes of 18 Oct 2026, seen through a real browser (Playwright,
Chromium) against a live server: what the owner, the cashier and a manager
actually see and click.

  * The owner makes "Rs 200 off above Rs 1,000" with the Setup form; it
    says Active, 0 uses. The form refuses a flat promo with no minimum.
  * The cashier picks it on the bill screen: the total drops by 200, the
    screen and the printed bill (print view, and the guest's WhatsApp link)
    name the promo.
  * A dish is voided below the minimum: the bill is not Rs 0; the screen
    says the promo is paused and why.
  * The owner sees 1 use and edits the promo; the bill keeps what it got.
  * A manager can't make all-outlet promos or change one.
  * No page throws a JavaScript error.

Skipped when Playwright or its Chromium isn't installed (CI has neither).
Screenshots go to PROMO_E2E_SHOTS (a folder) when it is set.

Run: python manage.py test promos.tests.test_promo_browser
"""
import os
from decimal import Decimal as D
from pathlib import Path
from unittest import SkipTest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from orders.services.void_service import void_order_item
from orders.views.public_views import make_public_bill_token
from promos.models import Promo
from tenants.models import Outlet, Tenant


# A browser on http://localhost: cookies that aren't secure-only or tied to rasova.net.
@override_settings(SESSION_COOKIE_SECURE=False, CSRF_COOKIE_SECURE=False,
                   SESSION_COOKIE_DOMAIN=None, CSRF_COOKIE_DOMAIN=None)
class PromoInTheBrowserTest(StaticLiveServerTestCase):

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise SkipTest("Playwright is not installed")
        # Playwright's sync API runs an event loop in this thread; the test's
        # own database calls are still plain synchronous ones.
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as e:
            cls.playwright.stop()
            super().tearDownClass()
            raise SkipTest(f"Chromium is not available: {e}")
        shots = os.environ.get("PROMO_E2E_SHOTS")
        cls.shots = Path(shots) if shots else None
        if cls.shots:
            cls.shots.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Browser Bistro")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.users = {
            role: User.objects.create_user(username=f"br_{role}", password="pw", role=role,
                                           tenant=self.tenant, outlet=self.outlet)
            for role in ("owner", "manager", "cashier")
        }
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.thali = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                             name="Royal Thali", price=D("1000"), gst_percentage=D("0"))
        self.lassi = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                             name="Lassi", price=D("200"), gst_percentage=D("0"))
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open",
                                          source="takeaway", created_by=self.users["owner"])
        for dish in (self.thali, self.lassi):
            OrderItem.objects.create(order=self.order, menu_item=dish, quantity=1, price=dish.price,
                                     gst_percentage=D("0"), total_price=dish.price, status="pending")
        self.order.recalculate_totals()
        # Made by the owner earlier, for every outlet: a manager may not touch it.
        Promo.objects.create(tenant=self.tenant, name="Festive Five", discount_type="percentage",
                             discount_value=D("5"))
        self.errors = []

    # ── helpers ─────────────────────────────────────────────────────

    def page_as(self, role):
        client = Client()
        client.force_login(self.users[role])
        context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                              "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                              "url": self.live_server_url}])
        page = context.new_page()
        page.set_default_timeout(15_000)
        page.on("pageerror", lambda error: self.errors.append(f"{role} {page.url}: {error}"))
        page.on("console", lambda msg: msg.type == "error" and "Failed to load resource" not in msg.text
                and self.errors.append(f"{role} {page.url}: {msg.text}"))
        return page

    def shot(self, page, name, **kwargs):
        if self.shots:
            page.screenshot(path=str(self.shots / f"{name}.png"), full_page=True, **kwargs)

    def row(self, page, name):
        return page.locator("#promo-tbody tr", has_text=name)

    def money_rows(self, page):
        return [" ".join(text.split()) for text in page.locator(".total-row").all_inner_texts()]

    def total(self, page):
        return " ".join(page.locator(".grand-total").inner_text().split())

    # ── the whole story ─────────────────────────────────────────────

    def test_a_promo_from_setup_to_the_bill(self):
        url = self.live_server_url

        # 1. The owner makes the promo with the real form.
        owner = self.page_as("owner")
        owner.goto(f"{url}/setup/promos/")
        owner.fill("#f-name", "Free coffee")
        owner.select_option("#f-type", "amount")
        owner.fill("#f-value", "100")
        owner.fill("#f-min", "0")
        owner.click("#create-btn")
        owner.wait_for_selector("#form-msg:has-text('minimum order of at least ₹100')")    # D3
        self.shot(owner, "01_flat_promo_refused")
        owner.goto(f"{url}/setup/promos/")
        owner.fill("#f-name", "Big Spender")
        owner.fill("#f-code", "BIG")
        owner.select_option("#f-type", "amount")
        owner.fill("#f-value", "200")
        owner.fill("#f-min", "1000")
        owner.click("#create-btn")
        owner.wait_for_selector("#promo-tbody tr:has-text('Big Spender')")
        big = self.row(owner, "Big Spender")
        self.assertEqual(big.locator("[data-status]").text_content().strip(), "Active")
        self.assertEqual(big.locator("[data-uses]").get_attribute("data-uses"), "0")
        self.shot(owner, "02_setup_created")

        # 2. The cashier picks it on the bill screen.
        cashier = self.page_as("cashier")
        bill_url = f"{url}/bill/{self.order.id}/"
        cashier.goto(bill_url)
        self.assertEqual(self.total(cashier), "TOTAL ₹1200.00")
        cashier.wait_for_function("document.querySelectorAll('#promo-select option').length > 1")
        index = cashier.evaluate("""() => [...document.querySelectorAll('#promo-select option')]
                                         .findIndex(o => o.textContent.includes('Big Spender'))""")
        self.assertGreater(index, 0)
        with cashier.expect_navigation():
            cashier.select_option("#promo-select", index=index)
        active = cashier.locator("[data-testid=active-discount]").inner_text()
        self.assertIn("Promo Big Spender (BIG)", active)
        self.assertIn("Promo BIG −₹200.00", self.money_rows(cashier))
        self.assertEqual(self.total(cashier), "TOTAL ₹1000.00")
        self.shot(cashier, "03_bill_with_promo")
        cashier.emulate_media(media="print")
        self.shot(cashier, "04_bill_printed")
        cashier.emulate_media(media="screen")

        # The guest's WhatsApp bill link names it too.
        guest = self.browser.new_page()
        guest.goto(f"{url}/bill/public/{make_public_bill_token(self.order.id)}/")
        self.assertIn("Promo BIG", guest.locator("body").inner_text())
        self.shot(guest, "05_whatsapp_bill")
        guest.close()

        # 3. The thali is voided: the bill falls below the minimum.
        void_order_item(self.users["manager"], self.order.items.get(menu_item=self.thali).id, "wrong dish")
        cashier.reload()
        self.assertEqual(self.total(cashier), "TOTAL ₹200.00")                    # not ₹0
        paused = cashier.locator("[data-testid=promo-paused]").inner_text()
        self.assertIn("Big Spender needs a bill of ₹1,000 (now ₹200)", paused)
        self.assertFalse([row for row in self.money_rows(cashier) if row.startswith("Promo")])
        self.shot(cashier, "06_bill_paused_below_minimum")

        # 4. The owner sees its use and edits it; the bill keeps what it got.
        owner.goto(f"{url}/setup/promos/")
        big = self.row(owner, "Big Spender")
        self.assertEqual(big.locator("[data-uses]").get_attribute("data-uses"), "1")
        owner.click("button[aria-label='Edit Big Spender']")
        self.assertEqual(owner.input_value("#f-value"), "200.00")
        owner.fill("#f-name", "Bigger Spender")
        owner.click("#create-btn")
        owner.wait_for_selector("#promo-tbody tr:has-text('Bigger Spender')")
        self.shot(owner, "07_setup_after_edit")
        cashier.reload()
        self.assertIn("Promo Big Spender (BIG)", cashier.locator("[data-testid=active-discount]").inner_text())

        # 5. A manager: no all-outlet switch, the owner's promo is read-only.
        manager = self.page_as("manager")
        manager.goto(f"{url}/setup/promos/")
        self.assertEqual(manager.locator("#all-outlets-toggle").count(), 0)
        self.assertEqual(manager.locator(".stat-card", has_text="Total Promos").locator(".stat-val").inner_text(), "2")
        self.assertIn("Owner", self.row(manager, "Festive Five").locator(".owner-only").inner_text())
        self.assertEqual(self.row(manager, "Bigger Spender").locator(".tbl-btn-edit").count(), 1)
        self.shot(manager, "08_setup_as_manager")

        self.assertEqual(self.errors, [])
