"""
The three carts (the POS, the QSR counter and the QR menu) show their totals
with static/js/cart_tax.js, whose maths test_cart_tax_js.py checks against the
real engine. This checks the wiring: each page loads the script, hands it the
outlet's real settings, and passes each dish's real rate (a 0% dish as 0,
never a made-up 5%) (P3, P10).

The POS and the QSR counter share one module now (static/js/billing/): it
gets the outlet's settings in the page-data block and each dish's rate on
its menu card, so that is what is checked for them. The module's own sums
are checked in static/js/tests/cart.test.mjs.

Run: python manage.py test orders.tests.test_cart_wiring
"""
import json
import re
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from setup.models import PaymentConfig
from menu.liquor import add_liquor_class
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant, TenantFeatureOverride
from tokens.models import TokenOrder


class CartWiringBase(TestCase):
    inclusive = False
    composition = False
    gstin = SAMPLE_GSTIN

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Cart Wiring Test", tenant_type="cafe")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant, name="Main", gst_no=self.gstin, gst_inclusive=self.inclusive,
            is_composition_scheme=self.composition, parcel_charge_amount=D("10"),
            parcel_gst_rate=D("18"),
        )
        PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet, cash_enabled=True)
        self.owner = User.objects.create_user(username="cart_wiring_owner", password="x",
                                              tenant=self.tenant, outlet=self.outlet, role="owner")
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.curd = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                            name="Curd", price=D("30"), gst_percentage=D("0"))
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.client = Client()
        self.client.force_login(self.owner)

    def pos(self):
        return self.client.get("/billing/").content.decode()

    def qsr(self):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                     source="counter", status="open")
        TokenOrder.objects.create(tenant=self.tenant, outlet=self.outlet, order=order,
                                  token_number=1, date=order.created_at.date())
        return self.client.get(f"/token/{order.id}/bill/").content.decode()

    def qr_menu(self):
        return Client().get(f"/menu/{self.table.qr_token}/").content.decode()

    def pos_outlet(self, page=None):
        """The outlet's settings as a billing page (the POS, or the QSR
        counter) hands them to its module."""
        match = re.search(r'<script id="page-data" type="application/json">(.*?)</script>', page or self.pos(), re.S)
        self.assertIsNotNone(match, "the page has no page-data block")
        return json.loads(match.group(1))["outlet"]


class GstExtraOutletTest(CartWiringBase):

    def test_every_cart_loads_the_shared_script(self):
        # The QR menu loads its module (since 13 Nov 2026), which works the
        # cart's total with cart_tax.js itself.
        qr = self.qr_menu()
        self.assertIn("js/qr_menu/index.js", qr)
        menu = (Path(settings.BASE_DIR) / "static" / "js" / "qr_menu" / "index.js").read_text(encoding="utf-8")
        self.assertIn('from "../cart_tax.js"', menu)
        self.assertIn("taxTotals(taxed", menu)
        # the POS and the counter load the module, whose cart imports cart_tax.js itself
        for page in (self.pos(), self.qsr()):
            self.assertIn("js/billing/index.js", page)
        cart = (Path(settings.BASE_DIR) / "static" / "js" / "billing" / "cart.js").read_text(encoding="utf-8")
        self.assertIn('from "../cart_tax.js"', cart)
        self.assertIn("taxTotals(lines", cart)

    def test_a_zero_percent_dish_is_passed_as_zero(self):
        pos, qsr, qr = self.pos(), self.qsr(), self.qr_menu()
        self.assertIn(f'data-id="{self.curd.id}" data-name="Curd" data-price="30.00" data-gst="0.00"', pos)
        self.assertIn(f'data-id="{self.curd.id}" data-name="Curd"', qsr)
        self.assertIn('data-price="30.00" data-gst="0.00"', qsr)
        self.assertIn(f'data-id="{self.curd.id}" data-name="Curd"', qr)
        self.assertIn('data-price="30.00"', qr)
        self.assertIn('data-gst="0.00"', qr)
        self.assertNotIn("|| 5", pos + qr)

    def test_the_outlet_settings_reach_the_script(self):
        pos, qsr = self.pos(), self.qsr()
        for page in (pos, qsr):
            self.assertEqual(self.pos_outlet(page), {
                "id": self.outlet.id, "inclusive": False, "vatInclusive": False, "composition": False,
                "gstRegistered": True, "parcelGstRate": 18.0, "parcelCharge": 10.0, "parcelPerItem": True,
            })

    def test_the_qr_cart_no_longer_claims_5_percent(self):
        qr = self.qr_menu()
        self.assertNotIn("GST (5%)", qr)
        self.assertRegex(qr, r"<span>GST</span>")


class GstIncludedOutletTest(CartWiringBase):
    inclusive = True

    def test_carts_say_the_gst_is_included(self):
        self.assertIs(self.pos_outlet()["inclusive"], True)
        self.assertIn("GST (incl.)", self.pos())
        self.assertIn("GST (included in prices)", self.qr_menu())


class CompositionOutletTest(CartWiringBase):
    composition = True

    def test_carts_show_no_gst_line(self):
        pos, qsr, qr = self.pos(), self.qsr(), self.qr_menu()
        self.assertIs(self.pos_outlet(pos)["composition"], True)
        self.assertIs(self.pos_outlet(qsr)["composition"], True)
        for page in (pos, qsr, qr):
            self.assertIsNone(re.search(r'class="view-gst"|id="billGst"', page))


class UnregisteredOutletTest(CartWiringBase):
    """No GSTIN, so no GST (P25): each cart is told, and shows no GST line."""
    gstin = None

    def test_carts_show_no_gst_line(self):
        pos, qsr, qr = self.pos(), self.qsr(), self.qr_menu()
        self.assertIs(self.pos_outlet(pos)["gstRegistered"], False)
        self.assertIs(self.pos_outlet(qsr)["gstRegistered"], False)
        self.assertIs(self.pos_outlet(qr)["gstRegistered"], False)
        for page in (pos, qsr, qr):
            self.assertIsNone(re.search(r'class="view-gst"|id="billGst"', page))


class LiquorOutletTest(CartWiringBase):
    """With the liquor_vat feature, each cart gets every dish's tax from the
    server (the dish-tax table, from the same rule the bill uses) and shows a
    VAT line only when there is VAT to show."""
    liquor_vat = True

    def setUp(self):
        super().setUp()
        if self.liquor_vat:
            TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        self.outlet.gst_no = "29ABCDE1234F1Z5"
        self.outlet.save(update_fields=["gst_no"])
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        self.beer = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=bar, name="Pint", price=D("250"),
            gst_percentage=D("0"), vat_class=add_liquor_class(self.outlet, "Beer"),
        )
        self.rum = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=bar, name="Rum", price=D("220"),
            gst_percentage=D("0"), vat_class=add_liquor_class(self.outlet, "Spirits", rate="5.5"),
        )

    def dish_tax(self, page):
        match = re.search(r'<script id="dish-tax" type="application/json">(.*?)</script>', page, re.S)
        self.assertIsNotNone(match, "the page has no dish tax table")
        return json.loads(match.group(1))

    def test_every_cart_is_told_each_dishs_tax(self):
        for name, page in (("pos", self.pos()), ("qsr", self.qsr()), ("qr", self.qr_menu())):
            with self.subTest(page=name):
                taxes = self.dish_tax(page)
                self.assertEqual(taxes[str(self.beer.id)], {"kind": "vat", "rate": "0.00"})
                self.assertEqual(taxes[str(self.rum.id)], {"kind": "vat", "rate": "5.50"})
                self.assertEqual(taxes[str(self.curd.id)], {"kind": "gst", "rate": "0.00"})
                if name == "qr":        # the QR menu's module reads it per dish
                    menu = (Path(settings.BASE_DIR) / "static" / "js" / "qr_menu" / "index.js").read_text(encoding="utf-8")
                    self.assertIn('readTable("dish-tax")', menu)
                    self.assertIn("taxLine(", menu)

    def test_every_cart_has_a_vat_line_hidden_until_there_is_vat(self):
        pos, qsr, qr = self.pos(), self.qsr(), self.qr_menu()
        self.assertIn('class="view-vat"', pos)
        for page in (pos, qsr):
            self.assertIn('class="view-vat"', page)
            self.assertIs(self.pos_outlet(page)["vatInclusive"], False)
            self.assertIn('<div class="summary-row vat-row is-hidden">', page)
        self.assertIn('id="billVat"', qr)
        self.assertIs(self.pos_outlet(qr)["vatInclusive"], False)


class LiquorFeatureOffCartTest(LiquorOutletTest):
    liquor_vat = False

    def test_every_cart_is_told_each_dishs_tax(self):
        for page in (self.pos(), self.qsr(), self.qr_menu()):
            self.assertEqual({tax["kind"] for tax in self.dish_tax(page).values()}, {"gst"})

    def test_every_cart_has_a_vat_line_hidden_until_there_is_vat(self):
        # the rows are there, and stay hidden: without the feature there is no VAT
        self.assertIn('class="view-vat"', self.pos())
