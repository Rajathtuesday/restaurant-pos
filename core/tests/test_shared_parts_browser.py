"""
The pages moved onto the shared post buttons and forms on 14 Nov 2026
(core/post_actions.js), in a real browser.

Why each one:
- Shift templates: Delete asked "are you sure?" and deleted even on Cancel
  (ui.confirm's answer is an object, always truthy).
- Pending refunds: Approve and Reject used confirm() and prompt(), which
  never show in the Android app, so a refund could not be decided there.
- Reservations: a refused change was reported with alert(), the same.
- The expense form, the shift and waiter buttons, the inventory report's
  tabs and period, the feature switches and New Restaurant: their inline
  handlers became markup the shared parts read; these check they still work.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test core.tests.test_shared_parts_browser
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from crm.models import Reservation
from finance.models import Expense
from payments.models import Refund
from shifts.models import ShiftTemplate
from tenants.models import Tenant, TenantFeatureOverride
from waiter.models import WaiterCall

User = get_user_model()

SHEET = 'return await t.until(() => document.querySelector(".swal2-popup:not(.swal2-toast) .swal2-confirm"));'
# Marks this page, so a step can wait for the page that replaces it (a reload).
MARK = 'document.body.dataset.before = "1";'
RELOADED = {"selector": "body:not([data-before])"}


def tap(selector):
    return "t.click(" + json.dumps(selector) + ");"


class SharedPartsBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "shared-parts")

    def assertNoErrors(self, out):
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))

    def test_cancelling_a_template_delete_keeps_it_and_confirming_deletes_it(self):
        # The same button on the old page and the new: the old one deleted on Cancel.
        sel = f'#tpl-{self.r.shift_template.id} button[title="Delete template"]'
        out = self.run_page("/shifts/templates/", [
            ("ask", tap(sel) + SHEET),
            ("cancel", 't.click(".swal2-cancel"); await new Promise((r) => setTimeout(r, 600));'
                       f'return Boolean(document.getElementById("tpl-{self.r.shift_template.id}"));'),
            ("ask again", tap(sel) + SHEET),
            ("delete", 't.click(".swal2-confirm");'
                       f'return await t.until(() => !document.getElementById("tpl-{self.r.shift_template.id}"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertTrue(out["results"]["cancel"])
        self.assertFalse(ShiftTemplate.objects.filter(id=self.r.shift_template.id).exists())

    def test_a_refund_is_rejected_with_no_reason_in_rasovas_own_sheet(self):
        self.assertEqual(self.r.refund.status, "pending")
        out = self.run_page("/refunds/pending/", [
            ("ask", tap(".btn-reject")
                    + 'return await t.until(() => document.querySelector(".swal2-input"));'),
            ("reject", MARK + 't.click(".swal2-confirm"); return true;', RELOADED),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.r.refund.refresh_from_db()
        self.assertEqual(self.r.refund.status, "rejected")

    def test_a_reservation_is_confirmed_by_its_button(self):
        self.assertEqual(self.r.reservation.status, "pending")
        day = self.r.reservation.reservation_time.date().isoformat()
        out = self.run_page(f"/crm/reservations/?date={day}", [
            ("confirm", MARK + tap(f'[data-url="/crm/reservations/{self.r.reservation.id}/status/"][title="Confirm"]')
                        + 'return true;', RELOADED),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.r.reservation.refresh_from_db()
        self.assertEqual(self.r.reservation.status, "confirmed")

    def test_an_expense_is_recorded_by_its_form_and_deleted_by_its_button(self):
        before = Expense.objects.filter(tenant=self.r.tenant).count()
        out = self.run_page("/finance/expenses/", [
            ("record", 'document.getElementById("exp-amount").value = "1234";'
                       'document.getElementById("exp-date").value = "2026-11-14";'
                       'document.getElementById("exp-notes").value = "Gas refill";'
                       't.click("#expenseForm button[type=submit]");'
                       'return await t.until(() => document.querySelector(".swal2-toast") && t.text(".swal2-toast"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertIn("Expense recorded", out["results"]["record"])
        self.assertEqual(Expense.objects.filter(tenant=self.r.tenant).count(), before + 1)
        self.assertTrue(Expense.objects.filter(notes="Gas refill", amount=Decimal("1234")).exists())

        out = self.run_page("/finance/expenses/", [
            ("ask", tap(f'[data-url="/finance/expenses/{self.r.expense.id}/delete/"]') + SHEET),
            ("delete", 't.click(".swal2-confirm");'
                       f'return await t.until(() => !document.getElementById("expense-row-{self.r.expense.id}"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertFalse(Expense.objects.filter(id=self.r.expense.id).exists())

    def test_the_waiter_resolves_a_call(self):
        call = WaiterCall.objects.get(id=self.r.waiter_call.id)
        self.assertFalse(call.is_resolved)
        out = self.run_page("/waiter-dashboard/", [
            ("resolve", MARK + tap(f'[data-url="/resolve-waiter/{call.id}/"]') + 'return true;', RELOADED),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        call.refresh_from_db()
        self.assertTrue(call.is_resolved)

    def test_the_inventory_report_tabs_and_the_custom_period(self):
        out = self.run_page("/reports/inventory/", [
            ("tab", tap('[data-action="section.show"][data-tab="cost"]')
                    + 'return [document.getElementById("tab-cost").hidden, document.getElementById("tab-ledger").hidden,'
                      ' document.getElementById("currentTab").value];'),
            ("custom", 'const s = document.querySelector("[data-action=\\"range.pick\\"]"); s.value = "custom";'
                       's.dispatchEvent(new Event("change", { bubbles: true }));'
                       'return getComputedStyle(document.getElementById("customRange")).display;'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["tab"], [False, True, "cost"])
        self.assertEqual(out["results"]["custom"], "flex")

    def test_a_feature_switch_saves_an_override(self):
        admin = User.objects.create_user(username="shared-admin", password="pw", is_superuser=True, is_staff=True)
        out = self.run_page(f"/settings/features/?tenant_id={self.r.tenant.id}", [
            ("toggle", 'const box = document.querySelector("[data-action=\\"feature.toggle\\"]");'
                       'const was = box.checked; box.click();'
                       'await t.until(() => !box.disabled && !document.querySelector(".saving"));'
                       'await t.until(() => document.querySelector(".swal2-toast"));'
                       'return [box.dataset.feature, was];'),
        ], user=admin)
        self.assertNoErrors(out)
        feature, was = out["results"]["toggle"]
        override = TenantFeatureOverride.objects.get(tenant=self.r.tenant, feature=feature)
        self.assertEqual(override.enabled, not was)

    def test_new_restaurant_opens_its_page(self):
        admin = User.objects.create_user(username="shared-admin2", password="pw", is_superuser=True, is_staff=True)
        out = self.run_page("/superuser/", [
            ("create", 'const f = document.getElementById("createForm");'
                       'f.querySelector("[name=name]").value = "Gamma Cafe";'
                       'f.querySelector("[name=owner_username]").value = "gamma_owner";'
                       'f.querySelector("[name=owner_password]").value = "gamma-pass-123";'
                       't.click("#createForm button[type=submit]"); return true;', "/superuser/tenant/"),
        ], user=admin)
        self.assertNoErrors(out)
        self.assertTrue(Tenant.objects.filter(name="Gamma Cafe").exists())
