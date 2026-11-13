"""
What the bill page is handed for its UPI QR and its payment buttons.

The UPI "scan & pay" QR used to be drawn once at page load from the whole
remaining balance, so after Split a guest scanning it was asked for the
full bill again, on screen and on the printed receipt. Until 13 November
2026 this file guarded that by reading the page's inline script as text,
because nothing here could run it ("that part was verified manually").

The page's code is a module now (static/js/bill/), and the behaviour is
tested for real: orders/tests/test_bill_browser.py types amounts, splits
the bill and reads the text inside the QR after each change, and
static/js/tests/bill.test.mjs checks the sums. What is left here is the
server's side: the page-data block gives the module the balance and the
UPI id only when there is something to collect, and the QR library loads
only then.

Run: python manage.py test orders.tests.test_bill_qr_amount_sync
"""
import json
import re
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from orders.models import Order
from setup.models import PaymentConfig
from tenants.models import Outlet, Tenant


class BillPageDataTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="QR Sync Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(username="qr_owner", password="pwd", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.cashier = User.objects.create_user(username="qr_cashier", password="pwd", role="cashier",
                                                tenant=self.tenant, outlet=self.outlet)
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="billing",
                                          grand_total=Decimal("357.00"))

    def _set_upi(self, upi_id="restaurant@okhdfcbank"):
        config, _ = PaymentConfig.for_outlet(self.outlet, self.tenant)
        config.upi_id = upi_id
        config.save(update_fields=["upi_id"])

    def page(self, user=None):
        self.client.force_login(user or self.owner)
        resp = self.client.get(reverse("bill-view", args=[self.order.id]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        data = json.loads(re.search(r'<script id="page-data" type="application/json">(.*?)</script>', html, re.S).group(1))
        return html, data

    def test_the_module_gets_the_balance_and_the_upi_id_to_draw_the_qr_from(self):
        self._set_upi()
        html, data = self.page()
        self.assertEqual((data["remaining"], data["upi_id"], data["payee"]),
                         ("357.00", "restaurant@okhdfcbank", "QR Sync Tenant"))
        self.assertEqual(data["bill_number"], str(self.order.display_number))
        self.assertIn('id="upi-qr-canvas"', html)
        self.assertIn("js/vendor/qrcode.min.js", html)
        self.assertIn("js/bill/index.js", html)

    def test_no_upi_id_no_qr_and_no_qr_library(self):
        html, data = self.page()
        self.assertEqual(data["upi_id"], "")
        self.assertNotIn('id="upi-qr-canvas"', html)
        self.assertNotIn("qrcode.min.js", html)
        self.assertIn('id="payAmount"', html, "the amount field and its change line work without UPI")

    def test_a_paid_bill_has_no_qr_to_scan(self):
        self._set_upi()
        Order.objects.filter(pk=self.order.pk).update(grand_total=Decimal("0"))
        html, data = self.page()
        self.assertEqual(data["upi_id"], "")
        self.assertNotIn("qrcode.min.js", html)

    def test_the_page_has_no_script_of_its_own(self):
        self._set_upi()
        html, _ = self.page()
        body = html.split("<body", 1)[1]
        inline = re.findall(r'<script(?![^>]*(?:\bsrc=|type="application/json"))[^>]*>', body)
        self.assertEqual(inline, [], "the bill page's code belongs in static/js/bill/")
        self.assertNotIn("onclick=", body)

    def test_only_a_cashier_is_held_on_an_unpaid_bill(self):
        _, as_cashier = self.page(self.cashier)
        _, as_owner = self.page(self.owner)
        self.assertIs(as_cashier["guard_back"], True)
        self.assertIs(as_owner["guard_back"], False)
