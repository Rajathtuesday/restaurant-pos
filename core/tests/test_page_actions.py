"""
The shared page buttons: print, reload, back, select a field
(static/js/core/page_actions.js).

core/app.js handles them on every base.html page, core/standalone.js on a
page without it. A page module that handled one of these names as well
would run it twice from one tap: two print dialogs. So no other module
may name them. And in a real browser, one tap prints once: on a page with
nothing of its own, on the bill page (which used to handle Print itself),
and on the purchase order printout (no base.html).

Run: python manage.py test core.tests.test_page_actions
"""
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant

SHARED = ("page.print", "page.reload", "page.back", "field.select", "form.submit", "range.pick", "post")
COUNT_PRINTS = 'window.__prints = 0; window.print = () => { window.__prints += 1; }; return true;'


class OneHandlerTest(SimpleTestCase):

    def test_only_page_actions_handles_the_shared_names(self):
        js = Path(settings.BASE_DIR) / "static" / "js"
        for path in js.rglob("*.js"):
            rel = path.relative_to(js).as_posix()
            if rel.startswith(("vendor/", "tests/")) or rel == "core/page_actions.js":
                continue
            text = path.read_text(encoding="utf-8")
            for name in SHARED:
                self.assertIsNone(re.search(r'["\']' + re.escape(name) + r'["\']\s*:', text),
                                  f"{rel} handles {name}; core/page_actions.js already does")


class PrintOnceBrowserTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "print-once")

    def prints(self, url):
        out = self.run_page(url, [
            ("stub", COUNT_PRINTS),
            ("tap", 't.click("[data-action=\\"page.print\\"]");'
                    'await new Promise((r) => setTimeout(r, 200)); return window.__prints;'),
        ], user=self.r.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]["tap"]

    def test_a_base_page_prints_once(self):
        self.assertEqual(self.prints("/reports/kitchen/"), 1)

    def test_the_bill_page_prints_once(self):
        self.assertEqual(self.prints(f"/bill/{self.r.order.id}/"), 1)

    def test_the_purchase_order_printout_prints_once(self):
        from inventory.models import PurchaseOrder, Supplier
        supplier = Supplier.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, name="Alpha Supplier")
        po = PurchaseOrder.objects.create(tenant=self.r.tenant, outlet=self.r.outlet, supplier=supplier,
                                          total_amount=Decimal("0"))
        self.assertEqual(self.prints(f"/inventory/purchase-orders/{po.id}/print/"), 1)
