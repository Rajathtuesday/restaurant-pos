"""
The mid-size pages moved to modules on 14 Nov 2026, in a real browser.

Why each one:
- The schedule builder deleted a shift even on Cancel (ui.confirm's answer
  is an object, always truthy), and put the staff member's name straight
  into an onclick: a name with an apostrophe (O'Brien) broke the button,
  so nobody could assign that person a shift.
- The pickup display board drew a token's text unescaped.
- GST rates, the token dashboard, outlet settings and suppliers: their
  inline handlers became modules and shared parts; these check they work.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test core.tests.test_mid_pages_browser
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from inventory.models import Supplier
from shifts.models import StaffSchedule

User = get_user_model()
SHEET = 'return await t.until(() => document.querySelector(".swal2-popup:not(.swal2-toast) .swal2-confirm"));'


def tap(selector):
    return "t.click(" + json.dumps(selector) + ");"


class MidPagesBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "mid-pages")

    def assertNoErrors(self, out):
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))

    def test_cancelling_a_shift_delete_keeps_it(self):
        chip = f"#chip-{self.r.schedule.id} button.chip-delete"   # the same button on the old page
        out = self.run_page("/shifts/schedule/", [
            ("ask", tap(chip) + SHEET),
            ("cancel", 't.click(".swal2-cancel"); await new Promise((r) => setTimeout(r, 800));'
                       f'return Boolean(document.getElementById("chip-{self.r.schedule.id}"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertTrue(out["results"]["cancel"])
        self.assertTrue(StaffSchedule.objects.filter(id=self.r.schedule.id).exists())

    def test_a_shift_is_assigned_to_someone_called_obrien(self):
        obrien = User.objects.create_user(username="obrien", password="pw", role="waiter", first_name="Asha",
                                          last_name="O'Brien", tenant=self.r.tenant, outlet=self.r.outlet)
        day = timezone.localdate().isoformat()
        cell = f"#cell-{obrien.id}-{day} .add-cell"                  # the same cell on the old page
        out = self.run_page("/shifts/schedule/", [
            ("open", tap(cell) + 'return await t.until(() => !document.getElementById("assignForm").hidden'
                                 ' && t.text("#f_staff_name"));'),
            ("assign", f'document.getElementById("f_template").value = "{self.r.shift_template.id}";'
                       't.click("#assignForm button[type=submit]");'
                       'return await t.until(() => document.querySelector(".swal2-toast") && t.text(".swal2-toast"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["open"], "Asha O'Brien")
        self.assertTrue(StaffSchedule.objects.filter(staff=obrien, date=timezone.localdate()).exists())

    def test_the_pickup_board_shows_a_ready_token_escaped(self):
        # The board lists paid tokens the kitchen has called ready.
        self.r.order.status = "paid"
        self.r.order.save(update_fields=["status"])
        self.r.token_order.ready_at = timezone.now()
        self.r.token_order.save(update_fields=["ready_at"])
        out = self.run_page(f"/display/{self.r.outlet.display_token}/", [
            ("tiles", 'return await t.until(() => document.querySelector("#tokenGrid .token-tile")'
                      ' && [t.all("#tokenGrid .token-tile").length, document.getElementById("emptyState").hidden]);'),
        ])
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["tiles"], [1, True])

    def test_a_dish_gst_rate_saves_when_changed(self):
        out = self.run_page("/menu/gst/", [
            ("change", f'const s = document.getElementById("gst-{self.r.menu_item.id}"); s.value = "18.00";'
                       's.dispatchEvent(new Event("change", { bubbles: true }));'
                       'return await t.until(() => document.querySelector(".swal2-toast") && t.text(".swal2-toast"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.r.menu_item.refresh_from_db()
        self.assertEqual(self.r.menu_item.gst_percentage, Decimal("18.00"))

    def test_a_ticked_outlet_option_lights_its_box(self):
        out = self.run_page("/setup/outlet/", [
            ("tick", 'const box = document.getElementById("comp-scheme-box"); const cb = box.querySelector("input");'
                     'const settled = () => new Promise((r) => setTimeout(r, 400));'   # the border fades 0.15 s
                     'cb.checked = false; await settled(); const off = getComputedStyle(box).borderTopColor;'
                     'cb.checked = true; await settled(); const on = getComputedStyle(box).borderTopColor; return off !== on;'),
            ("gstin", 'const g = document.getElementById("gst_no"); g.value = "27aab"; g.dispatchEvent(new Event("input", { bubbles: true }));'
                      'g.focus(); g.blur(); return [g.value, g.classList.contains("field-invalid")];'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertTrue(out["results"]["tick"])
        self.assertEqual(out["results"]["gstin"], ["27AAB", True])

    def test_a_supplier_is_added_by_its_form(self):
        out = self.run_page("/inventory/suppliers/", [
            ("add", 'document.getElementById("s_name").value = "Gamma Greens";'
                    't.click("form[data-action=\\"post\\"] button[type=submit]");'
                    'return await t.until(() => document.querySelector(".swal2-toast") && t.text(".swal2-toast"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertTrue(Supplier.objects.filter(tenant=self.r.tenant, name="Gamma Greens").exists())
