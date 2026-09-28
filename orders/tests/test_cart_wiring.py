"""
The three carts (the POS, the QSR counter and the QR menu) show their totals
with static/js/cart_tax.js, whose maths test_cart_tax_js.py checks against the
real engine. This checks the wiring: each page loads the script, hands it the
outlet's real settings, and passes each dish's real rate (a 0% dish as 0,
never a made-up 5%) (P3, P10).

Run: python manage.py test orders.tests.test_cart_wiring
"""
import json
import re
from decimal import Decimal as D

from django.test import Client, TestCase

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from setup.models import PaymentConfig
from menu.liquor import add_liquor_class
from tenants.models import Outlet, Tenant, TenantFeatureOverride
from tokens.models import TokenOrder


class CartWiringBase(TestCase):
    inclusive = False
    composition = False

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Cart Wiring Test", tenant_type="cafe")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant, name="Main", gst_inclusive=self.inclusive,
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


class GstExtraOutletTest(CartWiringBase):

    def test_every_cart_loads_the_shared_script(self):
        for page in (self.pos(), self.qsr(), self.qr_menu()):
            self.assertIn("js/cart_tax.js", page)
            self.assertIn("RasovaCartTax.totals(", page)

    def test_a_zero_percent_dish_is_passed_as_zero(self):
        pos, qsr, qr = self.pos(), self.qsr(), self.qr_menu()
        self.assertIn(f"openItemModal('{self.curd.id}', 'Curd', '30.00', '0.00'", pos)
        self.assertIn(f"addDirectly('{self.curd.id}', 'Curd', 30.00, 0.00,", qsr)
        self.assertRegex(qr, rf"addItem\('{self.curd.id}', 'Curd', 30\.00, \d+, 0\.00\)")
        self.assertNotIn("|| 5", pos + qr)

    def test_the_outlet_settings_reach_the_script(self):
        pos, qsr = self.pos(), self.qsr()
        self.assertIn("inclusive: false", pos)
        self.assertIn("composition: false", pos)
        self.assertIn("parcelGstRate: 18.00", pos)
        self.assertIn("const IS_COMPOSITION = false;", qsr)
        self.assertIn("const PARCEL_GST_RATE = 18.00;", qsr)

    def test_the_qr_cart_no_longer_claims_5_percent(self):
        qr = self.qr_menu()
        self.assertNotIn("GST (5%)", qr)
        self.assertRegex(qr, r"<span>GST</span>")


class GstIncludedOutletTest(CartWiringBase):
    inclusive = True

    def test_carts_say_the_gst_is_included(self):
        self.assertIn("inclusive: true", self.pos())
        self.assertIn("GST (incl.)", self.pos())
        self.assertIn("GST (included in prices)", self.qr_menu())


class CompositionOutletTest(CartWiringBase):
    composition = True

    def test_carts_show_no_gst_line(self):
        pos, qsr, qr = self.pos(), self.qsr(), self.qr_menu()
        self.assertIn("composition: true", pos)
        self.assertIn("const IS_COMPOSITION = true;", qsr)
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
                self.assertIn("RasovaCartTax.line(", page)

    def test_every_cart_has_a_vat_line_hidden_until_there_is_vat(self):
        pos, qsr, qr = self.pos(), self.qsr(), self.qr_menu()
        self.assertIn('class="view-vat"', pos)
        self.assertIn("vatInclusive: false", pos)
        self.assertIn('class="view-vat"', qsr)
        self.assertIn("const VAT_INCLUSIVE = false;", qsr)
        self.assertIn('id="billVat"', qr)
        self.assertIn("vatInclusive: false", qr)
        for page in (pos, qsr):
            self.assertIn("t.vat > 0", page)


class LiquorFeatureOffCartTest(LiquorOutletTest):
    liquor_vat = False

    def test_every_cart_is_told_each_dishs_tax(self):
        for page in (self.pos(), self.qsr(), self.qr_menu()):
            self.assertEqual({tax["kind"] for tax in self.dish_tax(page).values()}, {"gst"})

    def test_every_cart_has_a_vat_line_hidden_until_there_is_vat(self):
        # the rows are there, and stay hidden: without the feature there is no VAT
        self.assertIn('class="view-vat"', self.pos())
