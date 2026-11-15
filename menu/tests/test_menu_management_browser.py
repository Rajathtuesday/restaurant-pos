"""
Menu management in a real browser, both pages (static/js/menu/management.js,
15 Nov 2026).

Why: the old inline scripts
- asked with the browser's confirm() before deleting a dish or a category
  (and before a branch sync); confirm() never shows in the Android app
  (Playwright, like the app, answers it with "no"), so nothing could be
  deleted there;
- reloaded after a delete whatever the answer, so a dish the server kept
  (it is on past bills) vanished from view with no word why;
- on the cafe and franchise page, cleared a dish's kitchen station and
  description each time the dish was edited.
Those tests fail on the old pages; selectors are ones both share.
Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test menu.tests.test_menu_management_browser
"""
from decimal import Decimal

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from menu.models import MenuCategory, MenuItem
from setup.models import KitchenStation
from tenants.models import Tenant

URL = "/menu/"
SHEET = 'return !!(await t.until(() => document.querySelector(".swal2-confirm")));'
TOAST = 'return await t.until(() => { const e = document.querySelector(".swal2-toast"); return e && e.textContent; });'


class MenuBrowserBase(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "menu-alpha")
        self.spare = MenuItem.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, category=self.r.category,
                                             name="Alpha Spare Dish", price=Decimal("120.00"))

    def _run(self, steps, refused=None):
        """`refused`: the address of the one request expected to be refused
        (the browser logs a refusal as a console error)."""
        out = self.run_page(URL, steps, user=self.r.owner)
        errors = [e for e in out["errors"] if not (refused and e.startswith("console.error") and e.endswith(refused))]
        self.assertEqual(errors, [], "the browser reported errors:" + chr(10) + chr(10).join(errors))
        return out["results"]


class FullMenuBrowserTest(MenuBrowserBase):

    def test_delete_a_dish_asks_in_rasovas_sheet(self):
        self._run([
            ("sheet", f'document.querySelector("[data-url$=\'/delete-item/{self.spare.id}/\'], '
                      f'[onclick=\\"deleteItem(\'{self.spare.id}\')\\"]").click();' + SHEET),
            ("deleted", 't.click(".swal2-confirm"); return true;',
             {"selector": f"body:not(:has(.item-name[data-item='{self.spare.id}'], .menu-item-row .btn-edit-dish[data-item='{self.spare.id}']))"}),
        ])
        self.assertFalse(MenuItem.objects.filter(pk=self.spare.pk).exists())

    def test_a_dish_on_past_bills_says_why_it_stays(self):
        results = self._run([
            ("sheet", f'document.querySelector("[data-url$=\'/delete-item/{self.r.menu_item.id}/\']").click();' + SHEET),
            ("toast", 't.click(".swal2-confirm");' + TOAST),
        ], refused=f"/menu/delete-item/{self.r.menu_item.id}/")
        self.assertIn("past bills", results["toast"])
        self.assertTrue(MenuItem.objects.filter(pk=self.r.menu_item.pk).exists())

    def test_delete_a_category_asks_in_rasovas_sheet(self):
        empty = MenuCategory.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Alpha Desserts")
        self._run([
            ("sheet", f'document.querySelector("#cat-{empty.id} .btn-delete").click();' + SHEET),
            ("deleted", 't.click(".swal2-confirm"); return true;', {"selector": f"body:not(:has(#cat-{empty.id}))"}),
        ])
        self.assertFalse(MenuCategory.objects.filter(pk=empty.pk).exists())

    def test_a_refused_ingredient_says_why(self):
        results = self._run([
            ("open", f'document.querySelector("[data-action=\'menu.recipe\'][data-item=\'{self.spare.id}\']").click();'
                     'return !!(await t.until(() => document.querySelector("#recipeModal.show")));'),
            ("toast", 'document.getElementById("recipeQty").value = "lots";'
                      't.click("[data-action=\'menu.saveRecipe\']");' + TOAST),
        ], refused="/menu/add-recipe/")
        self.assertIn("quantity", results["toast"].lower())
        self.assertFalse(self.spare.recipes.exists())

    def test_edit_a_dish(self):
        self._run([
            ("open", f'document.querySelector("[data-action=\'menu.editDish\'][data-item=\'{self.spare.id}\']").click();'
                     'return await t.until(() => document.querySelector("#itemModal.show") && document.getElementById("itemName").value);'),
            ("save", 'document.getElementById("itemPrice").value = "135";'
                     't.click("#saveItemBtn"); return true;', {"selector": "body:not(:has(#itemModal.show))"}),
        ])
        self.spare.refresh_from_db()
        self.assertEqual(self.spare.price, Decimal("135.00"))

    def test_a_dishs_actions_sit_on_one_row_and_are_styled(self):
        # 15 Nov 2026: .btn-action-icon had no style at all, so the browser
        # drew grey native buttons and Delete wrapped onto a second row.
        results = self._run([
            ("row", f'const row = document.querySelector(".btn-edit-dish[data-item=\'{self.spare.id}\']").closest(".btn-group-luxury");'
                    'const buttons = Array.from(row.querySelectorAll("button"));'
                    'return {tops: new Set(buttons.map((b) => Math.round(b.getBoundingClientRect().top))).size,'
                    ' backgrounds: buttons.map((b) => getComputedStyle(b).backgroundColor),'
                    ' count: buttons.length};'),
        ])
        row = results["row"]
        self.assertGreaterEqual(row["count"], 4)
        self.assertEqual(row["tops"], 1, "the actions wrapped onto more than one row")
        self.assertEqual(set(row["backgrounds"]), {"rgba(0, 0, 0, 0)"}, "drawn as native grey buttons")

    def test_search_hides_other_dishes(self):
        results = self._run([
            ("search", 'const box = document.getElementById("menuManagementSearch"); box.value = "spare";'
                       'box.dispatchEvent(new Event("input", {bubbles: true}));'
                       'return Array.from(document.querySelectorAll(".menu-item-row"))'
                       '.filter((r) => r.offsetParent !== null).map((r) => r.querySelector(".item-name").textContent.trim());'),
        ])
        self.assertEqual(len(results["search"]), 1)
        self.assertIn("Spare", results["search"][0])


class QsrMenuBrowserTest(MenuBrowserBase):

    def setUp(self):
        super().setUp()
        Tenant.objects.filter(pk=self.r.tenant.pk).update(tenant_type="cafe")
        self.bar = KitchenStation.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Alpha Bar")
        MenuItem.objects.filter(pk=self.spare.pk).update(station=self.bar, description="House special")

    def test_an_edit_keeps_the_station_and_description(self):
        self._run([
            ("open", f'document.querySelector("#toggle-{self.spare.id}").closest(".menu-item-row")'
                     '.querySelector(".item-price").click();'
                     'return await t.until(() => document.querySelector("#itemModal.show") && document.getElementById("itemName").value);'),
            ("save", 'document.getElementById("itemPrice").value = "140";'
                     't.click("#saveItemBtn"); return true;', {"selector": "body:not(:has(#itemModal.show))"}),
        ])
        self.spare.refresh_from_db()
        self.assertEqual((self.spare.price, self.spare.station_id, self.spare.description),
                         (Decimal("140.00"), self.bar.id, "House special"))

    def test_the_availability_switch_shows_offline(self):
        results = self._run([
            ("off", f'document.getElementById("toggle-{self.spare.id}").click();'
                    f'return await t.until(() => !document.getElementById("badge-{self.spare.id}").hidden'
                    f' || getComputedStyle(document.getElementById("badge-{self.spare.id}")).display !== "none");'),
        ])
        self.assertTrue(results["off"])
        self.spare.refresh_from_db()
        self.assertFalse(self.spare.is_available)
