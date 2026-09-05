"""
The UPI "scan & pay" QR on the bill page used to be drawn exactly once at
page load, from the order's full remaining balance at that moment -- so
splitting the bill (or any manual edit to the payment amount) never
updated it. A customer scanning it after a split payment would be asked
to pay the full remaining balance again, on top of what was already
collected. The same stale QR is also what gets printed onto the physical
receipt (see the @media print rule for #upi-qr-wrap), so the bug reached
paper too, not just the screen.

These tests check the RENDERED TEMPLATE's JavaScript structure -- that
renderUpiQR is a real, callable function (not the old run-once anonymous
block), and that updateChange()/splitPay() are correctly wired to call
it. There is no browser/JS test runner in this project, so these cannot
execute the JavaScript itself or verify a canvas actually redraws in a
live browser -- that part was verified manually. What these tests do
guarantee: the correct code is present and wired together, so a future
edit that accidentally reverts to the old run-once pattern, or breaks the
call chain splitPay -> updateChange -> renderUpiQR, gets caught here
instead of silently shipping.

Run: python manage.py test orders.tests.test_bill_qr_amount_sync
"""
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from tenants.models import Tenant, Outlet, TenantFeatureOverride
from orders.models import Order
from setup.models import PaymentConfig


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="QR Sync Tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="qr_owner", password="pwd",
            role="owner", tenant=self.tenant, outlet=self.outlet,
        )
        self.client.force_login(self.owner)
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, status="billing",
            grand_total=Decimal("357.00"),
        )

    def _set_upi(self, upi_id="restaurant@okhdfcbank"):
        config, _ = PaymentConfig.for_outlet(self.outlet, self.tenant)
        config.upi_id = upi_id
        config.save(update_fields=["upi_id"])

    def _get_bill_html(self):
        resp = self.client.get(reverse("bill-view", args=[self.order.id]))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()


class RenderUpiQRFunctionTests(_Base):
    """The QR must be a real, re-callable function now -- not the old
    run-once anonymous block that only ever drew it at page load."""

    def test_renderUpiQR_is_defined_as_a_named_function(self):
        self._set_upi()
        html = self._get_bill_html()
        self.assertIn("function renderUpiQR(amount)", html)

    def test_initial_render_call_uses_the_real_remaining_balance(self):
        self._set_upi()
        html = self._get_bill_html()
        self.assertIn(f"renderUpiQR({self.order.grand_total});", html)

    def test_canvas_is_cleared_before_redrawing(self):
        """Without this, calling renderUpiQR a second time (after a split)
        would stack a second QR canvas next to the first instead of
        replacing it."""
        self._set_upi()
        html = self._get_bill_html()
        self.assertIn("canvasWrap.innerHTML = ''", html)

    def test_fallback_is_reset_before_redrawing(self):
        """A failed first draw shouldn't leave the fallback text stuck on
        screen forever if a later redraw actually succeeds."""
        self._set_upi()
        html = self._get_bill_html()
        self.assertIn("fallback.style.display = 'none';", html)


class UpdateChangeWiringTests(_Base):
    """updateChange() is the single hook point every amount-changing action
    already goes through -- confirms it actually calls renderUpiQR now."""

    def test_updateChange_calls_renderUpiQR(self):
        self._set_upi()
        html = self._get_bill_html()
        self.assertIn("if (typeof renderUpiQR === 'function') renderUpiQR(entered);", html)

    def test_no_upi_configured_means_renderUpiQR_never_renders(self):
        """Confirms the premise behind the typeof guard: with no UPI id,
        the entire QR script block -- including renderUpiQR itself -- is
        genuinely absent, not just empty."""
        # deliberately not calling self._set_upi()
        html = self._get_bill_html()
        self.assertNotIn("function renderUpiQR", html)

    def test_updateChange_itself_still_renders_with_no_upi_configured(self):
        """The typeof guard exists specifically so this still works cleanly
        when renderUpiQR doesn't exist at all."""
        html = self._get_bill_html()
        self.assertIn("function updateChange(enteredVal)", html)
        self.assertIn("if (typeof renderUpiQR === 'function') renderUpiQR(entered);", html)


class SplitPayWiringTests(_Base):
    """splitPay() previously set the payment field's value directly, which
    doesn't fire the field's oninput handler -- so neither the change-due
    display nor (now) the QR ever reacted to a split. Confirms the fix."""

    def test_splitPay_calls_updateChange_after_setting_the_field(self):
        self._set_upi()
        html = self._get_bill_html()
        self.assertIn("updateChange(share);", html)

    def test_splitPay_still_computes_the_correct_per_person_share(self):
        """The actual math (v / n) is untouched by this fix -- confirms the
        fix didn't accidentally change the calculation itself."""
        self._set_upi()
        html = self._get_bill_html()
        self.assertIn("(v / n).toFixed(2)", html)

    def test_splitPay_sets_the_field_before_calling_updateChange(self):
        """Order matters: updateChange reads whatever's already in the
        field indirectly via its own parameter, but the field itself must
        be set first so the UI shows the same value being applied."""
        self._set_upi()
        html = self._get_bill_html()
        set_pos = html.index("document.getElementById('payAmount').value = share;")
        call_pos = html.index("updateChange(share);")
        self.assertLess(set_pos, call_pos)


class RazorpayQRWiringTests(_Base):
    """The Razorpay QR path had the same bug in a different shape: it never
    read the split/payment field at all, so it always billed the order's
    full remaining balance no matter what the cashier had just split it
    into. Confirms openRazorpayQR() now sends the field's value."""

    def _enable_razorpay(self):
        TenantFeatureOverride.objects.create(
            tenant=self.tenant, feature="razorpay_gateway", enabled=True
        )
        config, _ = PaymentConfig.for_outlet(self.outlet, self.tenant)
        config.razorpay_enabled = True
        config.save(update_fields=["razorpay_enabled"])

    def test_openRazorpayQR_sends_the_payAmount_field_value(self):
        self._enable_razorpay()
        html = self._get_bill_html()
        create_qr_url = reverse("razorpay-create-qr", args=[self.order.id])
        self.assertIn("const amtEl  = document.getElementById('payAmount');", html)
        expected_call = "apiClient.post('" + create_qr_url + "', { amount });"
        self.assertIn(expected_call, html)

    def test_openRazorpayQR_falls_back_to_full_remaining_when_field_empty(self):
        self._enable_razorpay()
        html = self._get_bill_html()
        self.assertIn(f"amtEl.value : {self.order.grand_total};", html)
