"""
The standalone pages load every stylesheet they name, in a real browser.

Why: phase 0 of the frontend plan (12 November 2026) left the fonts
<link> unclosed in seven templates that do not extend base.html. The
browser swallowed the tag after it: on the QR menu that was <style>, so
the guest saw the whole stylesheet printed as text above the menu; on
login and the running order it was a stylesheet (Bootstrap's icons,
Bootstrap itself) that simply never loaded. Every page still answered
200, so no test saw it. core/tests/test_template_tags_closed.py guards
the cause in every template; this opens the pages and checks the effect.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test core.tests.test_page_styles_browser
"""
from decimal import Decimal as D

from accounts.models import User
from core.tests.browser import BrowserTestCase
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from tenants.models import Outlet, Tenant

STYLES = """
await t.until(() => document.readyState === "complete");
const loaded = Array.from(document.styleSheets).map((s) => s.href || "inline")
  .map((h) => h === "inline" ? h : new URL(h).pathname.replace(/^\\/static\\//, "").replace(/\\.[0-9a-f]{12}\\./, "."));
const body = document.body.innerText;
return {
  loaded,
  css_as_text: /:root\\s*\\{|--bg-color|\\{\\s*display:/.test(body),
  font: getComputedStyle(document.body).fontFamily,
};
"""


class PageStylesTest(BrowserTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Spice Garden", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Branch")
        self.owner = User.objects.create_user(username="styles_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.outlet)
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Breads")
        self.dish = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                            name="Butter Naan", price=D("50"), gst_percentage=D("0"))
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")

    def look(self, url, user=None):
        out = self.run_page(url, [("styles", STYLES)], user=user)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]["styles"]

    def test_the_qr_menu_is_styled_not_printed(self):
        seen = self.look(f"/menu/digital-menu/?table_token={self.table.qr_token}")
        self.assertFalse(seen["css_as_text"], "the QR menu shows its stylesheet as text")
        self.assertIn("inline", seen["loaded"], "the page's own <style> block did not load")
        self.assertIn("vendor/fonts/fonts.css", seen["loaded"])
        self.assertIn("Outfit", seen["font"])

    def test_the_qr_menu_from_a_staff_login_too(self):
        seen = self.look("/menu/qr/", user=self.owner)
        self.assertFalse(seen["css_as_text"])
        self.assertIn("inline", seen["loaded"])

    def test_login_loads_its_icons(self):
        seen = self.look("/login/")
        self.assertIn("vendor/fonts/fonts.css", seen["loaded"])
        self.assertIn("vendor/bootstrap-icons/bootstrap-icons.min.css", seen["loaded"])

    def test_the_running_order_loads_bootstrap_and_lists_its_dishes(self):
        """Also found here: the page called its first refresh while still
        parsing, before order_edit.js (a module) had set OrderEdit. It
        threw, the 3-second refresh never started, and the order showed
        no dishes at all."""
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, status="open", source="dine_in",
                                     table=self.table, created_by=self.owner)
        OrderItem.objects.create(order=order, menu_item=self.dish, quantity=1, price=D("50"),
                                 gst_percentage=D("0"), total_price=D("50"), status="sent")
        out = self.run_page(f"/order/{order.id}/", [
            ("styles", STYLES),
            ("dishes", 'return await t.until(() => document.getElementById("items").textContent.includes("Butter Naan"));'),
        ], user=self.owner)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        self.assertIn("vendor/fonts/fonts.css", out["results"]["styles"]["loaded"])
        self.assertIn("vendor/bootstrap/bootstrap.min.css", out["results"]["styles"]["loaded"])
        self.assertTrue(out["results"]["dishes"])
