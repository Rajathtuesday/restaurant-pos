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
            ("open", tap(f'button[title="Link inventory"]')
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
