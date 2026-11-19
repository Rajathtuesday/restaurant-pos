"""
The QR menu's cart, run for real in a browser: a guest taps ADD and VIEW
CART, and the cart's numbers are the bill's numbers.

Why: from 28 Sep to 3 Oct 2026 VIEW CART did nothing on every QR menu (the
cart read each dish's tax from a table the page never defined). This test
used to run the page's inline script in Node through its onclick handlers
(menu/tests/test_qr_cart_js.py); since 13 Nov 2026 the page is modules
(static/js/qr_menu/), so it runs in Chromium instead, with the same dishes
and the same numbers, written the one way money is written now.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test menu.tests.test_qr_cart_browser
"""
from decimal import Decimal as D

from core.tests.browser import BrowserTestCase
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from orders.models import Table
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant, TenantFeatureOverride

TAP = """
const add = (name) => t.all(".item-card").find((c) => c.dataset.name === name)
  .querySelector('[data-action="dish.add"]').click();
NAMES.forEach(add);
t.click('[data-action="cart.open"]');
await t.until(() => document.getElementById("cartModal").classList.contains("active"));
const text = (id) => { const e = document.getElementById(id); return e ? e.textContent.trim() : null; };
const shownRow = (id) => { const e = document.getElementById(id); return !!e && e.offsetParent !== null; };
return {
  rows: t.all("#cartItemsContainer .cart-item-row").length,
  subtotal: text("billSubtotal"), gst: text("billGst"), vat: text("billVat"), offers: text("billOffers"),
  total: text("billTotal"), vatShown: shownRow("billVatRow"), offersShown: shownRow("billOffersRow"),
  notes: t.all("#cartItemsContainer .cart-offer").map((e) => e.textContent.trim()),
};
"""


class QrCartRunsTest(BrowserTestCase):
    """A registered outlet, prices without GST: Curd (30, 0%) and Paneer
    Tikka (200, 5%)."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Cart Wiring Test", tenant_type="cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no=SAMPLE_GSTIN, gst_inclusive=False)
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.curd = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                            name="Curd", price=D("30"), gst_percentage=D("0"))
        self.paneer = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                              name="Paneer Tikka", price=D("200"), gst_percentage=D("5"))

    def tap_through(self, *dishes):
        names = "[" + ", ".join(f'"{d.name}"' for d in dishes) + "]"
        out = self.run_page(f"/menu/digital-menu/?table_token={self.table.qr_token}",
                            [("cart", f"const NAMES = {names};" + TAP)])
        self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
        return out["results"]["cart"]

    def test_view_cart_opens_the_cart_with_its_dishes_and_total(self):
        cart = self.tap_through(self.paneer, self.curd)
        self.assertEqual(cart["rows"], 2)
        # 5% on the paneer only: the curd is 0%
        self.assertEqual((cart["subtotal"], cart["gst"], cart["total"]), ("₹230", "₹10", "₹240"))


class QrPubCartRunsTest(QrCartRunsTest):
    """With the liquor_vat feature: the rum's 5.5% VAT can only come from the
    page's dish tax table (its GST rate is 0), so this proves the cart reads it."""

    def setUp(self):
        super().setUp()
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        self.rum = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=bar, name="Rum", price=D("220"),
            gst_percentage=D("0"), vat_class=add_liquor_class(self.outlet, "Spirits", rate="5.5"),
        )

    def test_the_cart_shows_the_liquor_vat(self):
        cart = self.tap_through(self.rum)
        self.assertEqual((cart["subtotal"], cart["vat"], cart["total"]), ("₹220", "₹12.10", "₹232.10"))
        self.assertTrue(cart["vatShown"])


class QrCartOffersTest(QrCartRunsTest):
    """A guest's cart shows the offer before ordering (static/js/offers.js),
    by the same rules the server bills with (offers/engine.py)."""

    def test_the_cart_takes_the_offer_off_before_tax(self):
        from offers.models import Offer, OfferTarget
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="offers", enabled=True)
        offer = Offer.objects.create(tenant=self.tenant, outlet=self.outlet, name="Paneer 25% off",
                                     kind="percent_off", percent=D("25"))
        OfferTarget.objects.create(offer=offer, menu_item=self.paneer)
        cart = self.tap_through(self.paneer, self.curd)
        # 200 less 50, GST 5% on the 150 left = 7.50; the curd is untouched.
        self.assertEqual((cart["subtotal"], cart["offers"], cart["gst"], cart["total"]),
                         ("₹230", "₹50", "₹7.50", "₹187.50"))
        self.assertTrue(cart["offersShown"])
        self.assertEqual(cart["notes"], ["Paneer 25% off −₹50"])   # one way, as the totals above (V12)

    def test_no_offers_no_row(self):
        cart = self.tap_through(self.paneer)
        self.assertFalse(cart["offersShown"])
