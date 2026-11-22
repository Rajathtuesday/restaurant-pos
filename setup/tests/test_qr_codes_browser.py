"""
Setup's QR codes are drawn on the page, never fetched from a third party.

Why: the table QR page and the printer setup page loaded their QR codes as
images from api.qrserver.com. Every table's private menu address went to an
outside service, and with no internet the codes were blank. They are drawn
now by the QR library Rasova serves itself (static/js/core/qr.js).

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test setup.tests.test_qr_codes_browser
"""
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant

DRAWN = """
await t.until(() => t.all(".qr-draw canvas").length + t.all(".qr-failed").length === t.all(".qr-draw").length);
const elsewhere = performance.getEntriesByType("resource").map((e) => e.name)
  .filter((name) => !name.startsWith(location.origin));
return { spots: t.all(".qr-draw").length, drawn: t.all(".qr-draw canvas").length,
         failed: t.all(".qr-failed").length, elsewhere };
"""


class QrCodesDrawnHereTest(BrowserTestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "qr-alpha")

    def look(self, url):
        out = self.run_page(url, [("qr", DRAWN)], user=self.r.owner)
        self.assertEqual(out["errors"], [], "\n".join(out["errors"]))
        return out["results"]["qr"]

    def test_every_tables_code_is_drawn_and_nothing_leaves_the_site(self):
        seen = self.look("/setup/qr-codes/")
        self.assertEqual(seen["drawn"], seen["spots"])
        self.assertGreaterEqual(seen["drawn"], 2, "both tables of the fixture")
        self.assertEqual((seen["failed"], seen["elsewhere"]), (0, []))

    def test_the_printers_pages_two_codes_are_drawn_too(self):
        seen = self.look("/setup/printers/")
        self.assertEqual((seen["spots"], seen["drawn"], seen["failed"], seen["elsewhere"]), (2, 2, 0, []))
