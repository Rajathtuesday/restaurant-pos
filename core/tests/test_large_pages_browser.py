"""
The large pages moved to modules on 14 Nov 2026, in a real browser.

Why each one:
- Setup, Promos deleted a promo even on Cancel (ui.confirm's answer is an
  object, always truthy), and its three tiles showed "-" until a script
  counted the rows; the server renders the numbers now.
- Modifier groups put ingredient names, which staff type, straight into
  the Inventory Link sheet as HTML.
- The recipe import review asked for a new ingredient with two prompt()s,
  which never show in the Android app.
- The sales report and the setup wizard: their inline scripts became
  modules; these check the charts draw and the web address is checked.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test core.tests.test_large_pages_browser
"""
import json
from decimal import Decimal

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from inventory.models import InventoryItem, ModifierRecipe
from promos.models import Promo

SHEET = 'return await t.until(() => document.querySelector(".swal2-popup:not(.swal2-toast) .swal2-confirm"));'


def tap(selector):
    return "t.click(" + json.dumps(selector) + ");"


class LargePagesBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "large-pages")

    def assertNoErrors(self, out):
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))

    def test_cancelling_a_promo_delete_keeps_it_and_the_tiles_are_numbers(self):
        out = self.run_page("/setup/promos/", [
            ("tiles", 'return [t.text("#active-count"), t.text("#alloutlet-count"), t.text("#uses-count")];'),
            ("ask", tap(".tbl-btn-del") + SHEET),          # the same button on the old page
            ("cancel", 't.click(".swal2-cancel"); await new Promise((r) => setTimeout(r, 800));'
                       f'return Boolean(document.getElementById("row-{self.r.promo.id}"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertTrue(all(v.isdigit() for v in out["results"]["tiles"]), out["results"]["tiles"])
        self.assertTrue(out["results"]["cancel"])
        self.assertTrue(Promo.objects.filter(id=self.r.promo.id, archived_at__isnull=True).exists())

    def test_an_ingredient_name_shows_as_text_in_the_link_sheet(self):
        salt = InventoryItem.objects.create(tenant=self.r.tenant, outlet=self.r.outlet,
                                            name='<b id="injected">Salt</b>', unit="g")
        ModifierRecipe.objects.create(modifier=self.r.modifier, inventory_item=salt, quantity_required=Decimal("2"), unit="g")
        out = self.run_page("/menu/modifiers/", [
            ("open", tap('button[title="Link inventory"]')
                     + 'return await t.until(() => document.getElementById("existingLinks").textContent.includes("Salt"));'),
            ("text", 'return [Boolean(document.getElementById("injected")),'
                     ' document.getElementById("existingLinks").textContent.includes("<b id=")];'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["text"], [False, True])

    def test_a_new_ingredient_is_added_from_the_recipe_review_in_one_sheet(self):
        self.r.recipe_job.status = "ready_for_review"
        self.r.recipe_job.save(update_fields=["status"])
        out = self.run_page(f"/inventory/recipe-import/{self.r.recipe_job.id}/review/", [
            ("ask", 'const s = document.querySelector(".match-select"); s.value = "__new__";'
                    's.dispatchEvent(new Event("change", { bubbles: true }));'
                    'return await t.until(() => document.getElementById("ni-name"));'),
            ("add", 'document.getElementById("ni-name").value = "Gamma Paneer";'
                    'document.getElementById("ni-unit").value = "kg"; t.click(".swal2-confirm");'
                    'return await t.until(() => document.querySelector(".match-select").selectedOptions[0].textContent.includes("Gamma Paneer")'
                    ' && document.querySelector(".match-select").selectedOptions[0].textContent);'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["add"], "Gamma Paneer (kg)")
        self.assertTrue(InventoryItem.objects.filter(tenant=self.r.tenant, name="Gamma Paneer", unit="kg").exists())

    def test_the_sales_report_draws_its_charts(self):
        out = self.run_page("/reports/dashboard/?date_filter=weekly", [
            ("charts", 'return await t.until(() => window.Chart && Chart.getChart(document.getElementById("salesChart"))'
                       ' && Chart.getChart(document.getElementById("paymentChart")) && true);'),
            ("export", 'return document.querySelector("[data-action=\\"report.export\\"][data-type=\\"orders\\"]").getAttribute("href");'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertTrue(out["results"]["charts"])
        self.assertIn("type=orders", out["results"]["export"])

    def test_the_wizard_checks_a_web_address_as_it_is_typed(self):
        out = self.run_page("/setup/onboard/?step=1", [
            ("edit", tap('[data-action="slug.edit"]') + 'return !document.getElementById("subdomainEdit").hidden;'),
            ("type", 'const i = document.getElementById("slugInput"); i.value = "Gamma Cafe!";'
                     'i.dispatchEvent(new Event("input", { bubbles: true }));'
                     'return await t.until(() => /available|taken|reserved|use/i.test(t.text("#slugStatus")) && [i.value, t.text("#slugStatus")]);'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertTrue(out["results"]["edit"])
        self.assertEqual(out["results"]["type"][0], "gammacafe")


class StaffAndInventoryBrowserTest(BrowserTestCase):
    """Setup Staff and the inventory board (14 Nov 2026).

    The inventory board's Generate Purchase Orders line put supplier and
    ingredient names, typed by staff, into the page as HTML; and the board
    said everything with alert(), which never shows in the Android app."""

    def setUp(self):
        self.r = Restaurant("Alpha", "staff-inv")

    def assertNoErrors(self, out):
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))

    def test_a_role_is_changed_from_its_sheet_and_a_deactivated_member_hides(self):
        out = self.run_page("/setup/staff/", [
            ("ask", tap(f'#staff-row-{self.r.staff.id} [data-action="staff.role"]')
                    + 'return await t.until(() => document.querySelector(".swal2-select"));'),
            ("pick", 'const s = document.querySelector(".swal2-select"); s.value = "cashier"; t.click(".swal2-confirm");'
                     'return await t.until(() => document.querySelector(".swal2-toast") && t.text(".swal2-toast"));'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.r.staff.refresh_from_db()
        self.assertEqual(self.r.staff.role, "cashier")

        self.r.staff.is_active = False
        self.r.staff.save(update_fields=["is_active"])
        out = self.run_page("/setup/staff/", [
            ("hidden", f'return getComputedStyle(document.getElementById("staff-row-{self.r.staff.id}")).display;'),
            ("shown", 'document.getElementById("showInactiveToggle").click();'
                      f'return getComputedStyle(document.getElementById("staff-row-{self.r.staff.id}")).display;'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        self.assertEqual(out["results"]["hidden"], "none")
        self.assertEqual(out["results"]["shown"], "table-row")

    def test_generated_purchase_orders_show_supplier_names_as_text(self):
        self.r.supplier.name = '<b id="injected">Fresh Farm</b>'
        self.r.supplier.save(update_fields=["name"])
        item = self.r.inventory_item
        item.stock, item.low_stock_threshold, item.reorder_quantity = Decimal("1"), Decimal("10"), Decimal("5")
        item.cost_price = Decimal("300")              # an item with no cost is skipped
        item.save(update_fields=["stock", "low_stock_threshold", "reorder_quantity", "cost_price"])
        out = self.run_page("/inventory/board/", [
            ("generate", tap("#genPoBtn") + 'return await t.until(() => t.text("#lowStockBarText").includes("Fresh Farm")'
                                             ' && [Boolean(document.getElementById("injected")), t.text("#lowStockBarText")]);'),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        injected, text = out["results"]["generate"]
        self.assertFalse(injected)
        self.assertIn('<b id="injected">Fresh Farm</b>', text)

    def test_stock_is_added_and_wastage_logged_by_their_forms(self):
        item = self.r.inventory_item
        before = item.stock
        out = self.run_page("/inventory/board/", [
            ("restock", f'document.getElementById("add{item.id}").value = "5";'
                        'document.body.dataset.before = "1";'
                        f't.click("#add{item.id} ~ button[type=submit]"); return true;', {"selector": "body:not([data-before])"}),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        out = self.run_page("/inventory/board/", [
            ("wait", f'return await t.until(() => document.getElementById("add{item.id}"));'),
            ("open", tap(f'[data-action="inv.waste"][data-id="{item.id}"]')
                     + 'return await t.until(() => document.getElementById("wastageModal").classList.contains("show"));'),
            ("log", 'document.getElementById("wastageQty").value = "2";'
                    'document.body.dataset.before = "1"; t.click("#wastageForm button[type=submit]"); return true;',
             {"selector": "body:not([data-before])"}),
        ], user=self.r.owner)
        self.assertNoErrors(out)
        item.refresh_from_db()
        self.assertEqual(item.stock, before + Decimal("5") - Decimal("2"))
