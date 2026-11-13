"""
The bill page in a real browser: the amount being collected, the UPI QR
that follows it, Split, taking cash, closing a bill unpaid.

Why: on 13 November 2026 the page's 540 lines of inline script became
modules (static/js/bill/). The module tests (static/js/tests/bill.test.mjs)
prove each piece with fakes; this proves the page, the modules, the QR
library and the server together.

It also replaces what orders/tests/test_bill_qr_amount_sync.py could only
do by reading the page's script as text ("there is no browser test runner
in this project, so that part was verified manually"). The bug those tests
guard: the UPI QR was drawn once at page load with the whole balance, so
after Split a guest scanning it was asked for the full bill again, on
screen and on the printed receipt. Here the QR's text is read after every
change of the amount.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test orders.tests.test_bill_browser
"""
from decimal import Decimal as D

from accounts.models import User
from core.tests.browser import BrowserTestCase
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem, Payment
from setup.models import PaymentConfig
from tenants.models import Outlet, Tenant, TenantFeatureOverride

# Record every text the page asks the QR library to draw.
WATCH_QR = """
window.qrTexts = [];
const draw = window.QRCode.toCanvas;
window.QRCode.toCanvas = (text, ...rest) => { window.qrTexts.push(text); return draw(text, ...rest); };
"""
AMOUNT_IN_QR = """
const last = window.qrTexts[window.qrTexts.length - 1] || "";
const amount = (last.match(/[?&]am=([^&]*)/) || [])[1];
"""
TYPE_AMOUNT = """
function typeAmount(value) {
  const field = document.getElementById("payAmount");
  field.value = value;
  field.dispatchEvent(new Event("input", { bubbles: true }));
}
"""


class BillPageBase(BrowserTestCase):
    tenant_name = "Salt & Pepper"

    def setUp(self):
        self.tenant = Tenant.objects.create(name=self.tenant_name)
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(username="bill_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        self.cashier = User.objects.create_user(username="bill_cashier", password="pw", role="cashier",
                                                tenant=self.tenant, outlet=self.outlet)
        config, _ = PaymentConfig.for_outlet(self.outlet, self.tenant)
        config.upi_id = "restaurant@okhdfcbank"
        config.cash_enabled = True
        config.save()
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                       name="Chilli Chicken", price=D("400"), gst_percentage=D("0"))
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open",
                                          source="dine_in", created_by=self.owner)
        OrderItem.objects.create(order=self.order, menu_item=dish, quantity=1, price=D("400"),
                                 gst_percentage=D("0"), total_price=D("400"), status="sent")
        self.order.recalculate_totals()
        self.order.status = "billing"
        self.order.save()

    def browse(self, steps, user=None, expect=()):
        """Run the steps on the bill page. `expect` lists the refusals the
        test sets up on purpose, by address ("400 (Bad Request) /pay/"):
        each must appear once, and nothing else may."""
        out = self.run_page(f"/bill/{self.order.id}/", steps, user=user or self.owner)
        unexpected = list(out["errors"])
        for refusal in expect:
            match = next((e for e in unexpected if refusal in e), None)
            self.assertIsNotNone(match, f"expected a refusal like {refusal!r}; the browser saw {out['errors']}")
            unexpected.remove(match)
        self.assertEqual(unexpected, [], "the browser reported errors:\n" + "\n".join(unexpected))
        return out["results"]


class AmountAndQrTest(BillPageBase):

    def test_the_upi_qr_follows_the_amount_being_collected(self):
        seen = self.browse([
            ("loaded", """
                await t.until(() => t.all("#upi-qr-canvas canvas").length === 1);
                return { field: document.getElementById("payAmount").value, total: t.text(".grand-total") };
            """),
            ("typed", WATCH_QR + TYPE_AMOUNT + """
                typeAmount("150");
            """ + AMOUNT_IN_QR + """
                return { amount, canvases: t.all("#upi-qr-canvas canvas").length, payee: (last.match(/pn=([^&]*)/) || [])[1],
                         parts: last.split("?")[1].split("&").map((p) => p.split("=")[0]) };
            """),
            ("pay_all", """
                t.click('[data-action="pay.all"]');
            """ + AMOUNT_IN_QR + """
                return { amount, field: document.getElementById("payAmount").value };
            """),
            ("split_nonsense", """
                t.click('[data-action="pay.split"]');
                const people = await t.until(() => document.getElementById("split-people"));
                people.value = "0";
                t.click(".swal2-confirm");
                const message = await t.until(() => t.text(".swal2-validation-message"));
                return { message, field: document.getElementById("payAmount").value };
            """),
            ("split", """
                document.getElementById("split-people").value = "3";
                t.click(".swal2-confirm");
                await t.until(() => document.getElementById("payAmount").value === "133.33");
            """ + AMOUNT_IN_QR + """
                return { amount, canvases: t.all("#upi-qr-canvas canvas").length,
                         change: document.getElementById("change-display").classList.contains("is-shown") };
            """),
            ("overpaid", TYPE_AMOUNT + """
                typeAmount("500");
                return { shown: getComputedStyle(document.getElementById("change-display")).display, change: t.text("#change-amount") };
            """),
        ])
        self.assertEqual(seen["loaded"]["field"], "400.00")
        self.assertEqual(seen["typed"]["amount"], "150")
        self.assertEqual(seen["typed"]["canvases"], 1, "a redraw must replace the QR, not stack a second one")
        # "Salt & Pepper": the ampersand is part of the name, not the start of another parameter
        self.assertEqual(seen["typed"]["payee"], "Salt%20%26%20Pepper")
        self.assertEqual(seen["typed"]["parts"], ["pa", "pn", "am", "cu", "tn"])
        self.assertEqual(seen["pay_all"], {"amount": "400", "field": "400.00"})
        self.assertEqual(seen["split_nonsense"], {"message": "Enter a whole number of people, 1 or more.", "field": "400.00"})
        # the bug this guards: after Split the QR still asked for the whole bill
        self.assertEqual(seen["split"], {"amount": "133.33", "canvases": 1, "change": False})
        self.assertEqual(seen["overpaid"], {"shown": "block", "change": "₹100"})

    def test_no_upi_id_no_qr_and_the_amount_still_works(self):
        PaymentConfig.objects.filter(outlet=self.outlet).update(upi_id="")
        seen = self.browse([
            ("typed", TYPE_AMOUNT + """
                typeAmount("450");
                return { box: !!document.getElementById("upi-qr-wrap"), library: typeof window.QRCode,
                         change: t.text("#change-amount"), shown: getComputedStyle(document.getElementById("change-display")).display };
            """),
        ])
        self.assertEqual(seen["typed"], {"box": False, "library": "undefined", "change": "₹50", "shown": "block"})


class PaymentTest(BillPageBase):

    def test_cash_is_taken_and_the_bill_is_paid(self):
        seen = self.browse([
            ("pay", """
                t.click('[data-action="pay.method"][data-method="cash"]');
                return true;
            """, {"selector": "#register-float, .paid-note"}),
            ("register", """
                const float = document.getElementById("register-float");
                if (float) { float.value = "500"; t.click(".swal2-confirm"); }
                return !!float;
            """, {"selector": ".paid-note"}),
            ("paid", """
                return { note: t.text(".paid-note"), balance: t.text(".balance-val"), buttons: t.all('[data-action="pay.method"]').length,
                         reloaded_for_receipt: location.search };
            """),
        ], expect=(
            # This restaurant is fine dining with no register open: the first try is refused,
            # the cashier opens the register on the bill page, and the same payment goes through.
            "400 (Bad Request) /pay/",
            # It has no network printer, so the server cannot print the bill itself.
            "400 (Bad Request) /print-bill/",
        ))
        self.assertTrue(seen["register"], "the register sheet was not offered")
        self.assertEqual(seen["paid"], {"note": "PAYMENT COMPLETE", "balance": "FULLY PAID", "buttons": 0,
                                        # no printer took it, so the page came back asking for the receipt page
                                        "reloaded_for_receipt": "?print=1"})
        from shifts.models import CashSession
        self.assertEqual(CashSession.objects.get(outlet=self.outlet, status="open").opening_balance, D("500"))
        self.order.refresh_from_db()
        self.assertIn(self.order.status, ("paid", "closed"))
        payment = Payment.objects.get(order=self.order)
        self.assertEqual((payment.method, payment.amount), ("cash", D("400.00")))

    def test_a_cashier_cannot_walk_away_from_an_unpaid_bill(self):
        seen = self.browse([
            ("back", """
                t.click(".nav-back");
                const toast = await t.until(() => t.text(".swal2-toast .swal2-title"));
                return { toast, path: location.pathname, close: !!document.getElementById("close-unpaid") };
            """),
        ], user=self.cashier)
        self.assertEqual(seen["back"], {"toast": "Complete payment before leaving",
                                        "path": f"/bill/{self.order.id}/", "close": False})


class CloseUnpaidTest(BillPageBase):

    def setUp(self):
        super().setUp()
        # A closed dine-in bill goes back to the floor plan: give it one to land on.
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="floor_plan", enabled=True)

    def test_closing_without_payment_needs_a_reason_and_records_it(self):
        seen = self.browse([
            ("no_reason", """
                t.click("#close-unpaid");
                await t.until(() => document.getElementById("close-reason"));
                t.click(".swal2-confirm");
                return { message: await t.until(() => t.text(".swal2-validation-message")), said: t.text(".swal2-html-container") };
            """),
            ("reason", """
                document.getElementById("close-reason").value = "guest walked out";
                t.click(".swal2-confirm");
                return true;
            """, "/tables/"),
            ("landed", "return { path: location.pathname, title: document.title };"),
        ])
        self.assertEqual(seen["landed"]["path"], "/tables/")
        self.assertEqual(seen["no_reason"]["message"], "Closing without payment needs a reason.")
        self.assertIn("₹400.00 stays unpaid", seen["no_reason"]["said"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "closed")
        self.assertFalse(Payment.objects.filter(order=self.order).exists())
        reasons = [str(e.metadata) for e in OrderEvent.objects.filter(order=self.order)]
        self.assertTrue(any("guest walked out" in r for r in reasons), reasons)
