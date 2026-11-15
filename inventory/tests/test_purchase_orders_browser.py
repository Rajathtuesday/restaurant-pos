"""
Purchase orders in a real browser (static/js/inventory/purchase_orders.js,
14 Nov 2026).

Why: the old inline script
- asked with the browser's confirm() before Order and Cancel; confirm()
  never shows in the Android app (Playwright, like the app, answers it with
  "no"), so a PO could not be sent or cancelled there;
- wrote ingredient names into the Receive drawer as HTML.
Those three tests fail on the old page; selectors are ones both share.
Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test inventory.tests.test_purchase_orders_browser
"""
from decimal import Decimal

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant
from inventory.models import InventoryItem, PurchaseOrder, PurchaseOrderItem, Supplier

URL = "/inventory/purchase-orders/"
SHEET = 'return !!(await t.until(() => document.querySelector(".swal2-confirm")));'


class PurchaseOrdersBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "po-alpha")
        self.po = self.r.purchase_order
        PurchaseOrderItem.objects.create(purchase_order=self.po, item=self.r.inventory_item,
                                         quantity=Decimal("5"), unit_price=Decimal("300"))

    def _run(self, steps):
        out = self.run_page(URL, steps, user=self.r.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]

    def _status(self):
        self.po.refresh_from_db()
        return self.po.status

    def test_order_asks_in_rasovas_sheet(self):
        self._run([
            ("sheet", 't.click(".btn-po-order, [onclick^=markOrdered]");' + SHEET),
            ("ordered", 't.click(".swal2-confirm"); return true;', {"selector": "body:not(:has(.btn-po-edit, [onclick^=openEditDrawer]))"}),
        ])
        self.assertEqual(self._status(), "ordered")

    def test_cancel_asks_in_rasovas_sheet(self):
        self._run([
            ("sheet", 't.click(".btn-po-cancel, [onclick^=cancelPO]");' + SHEET),
            ("cancelled", 't.click(".swal2-confirm"); return true;', {"selector": "body:not(:has(.btn-po-edit, [onclick^=openEditDrawer]))"}),
        ])
        self.assertEqual(self._status(), "cancelled")

    def test_an_ingredient_name_shows_as_text_when_receiving(self):
        InventoryItem.objects.filter(pk=self.r.inventory_item.pk).update(name='<b id="injected">Paneer</b>')
        PurchaseOrder.objects.filter(pk=self.po.pk).update(status="ordered")
        results = self._run([
            ("open", 't.click(".btn-po-receive, [onclick^=openReceiveModal]");'
                     'return await t.until(() => document.querySelector("#receiveLines .receive-line"));'),
            ("injected", 'return document.querySelector("#receiveLines #injected") !== null;'),
            ("text", 'return t.text("#receiveLines").includes("<b id=");'),
        ])
        self.assertFalse(results["injected"])
        self.assertTrue(results["text"])

    def test_receiving_updates_the_order(self):
        PurchaseOrder.objects.filter(pk=self.po.pk).update(status="ordered")
        self._run([
            ("open", 't.click(".btn-po-receive");'
                     'return await t.until(() => document.querySelector("#receiveLines .recv-qty"));'),
            ("receive", 'document.querySelector("#receiveLines .recv-qty").value = "2";'
                        't.click("#submitReceiveBtn"); return true;', {"selector": ".pill-partially_received"}),
        ])
        self.assertEqual(self._status(), "partially_received")

    def test_a_new_po_from_the_drawer(self):
        other = Supplier.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Alpha Dairy")
        self._run([
            ("open", 't.click("[data-action=\'po.new\']"); return true;'),
            ("fill", f'document.getElementById("po_supplier").value = "{other.id}";'
                     'const row = document.querySelector("#lineItems .line-item-row");'
                     f'const sel = row.querySelector(".item-sel"); sel.value = "{self.r.inventory_item.id}";'
                     'sel.dispatchEvent(new Event("change", {bubbles: true}));'
                     'row.querySelector(".line-qty").value = "3";'
                     'row.querySelector(".line-price").value = "250";'
                     'row.querySelector(".line-qty").dispatchEvent(new Event("input", {bubbles: true}));'
                     'return t.text("#poTotal");'),
            ("create", 't.click("#submitPoBtn"); return true;',
             {"selector": "body:has(tbody tr:nth-of-type(2))"}),
        ])
        self.assertEqual(PurchaseOrder.objects.filter(tenant=self.r.tenant).count(), 2)
