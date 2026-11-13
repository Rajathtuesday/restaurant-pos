"""
The kitchen display in a real browser: the tickets, starting a dish,
marking it ready, bumping a ticket, picking a station, clearing the
screen, the kitchen's own light switch.

Why: on 13 November 2026 the page's 151 lines of inline script became
modules (static/js/kitchen/). The module tests (static/js/tests/kitchen.test.mjs)
prove each piece with fakes; this proves the page, the modules and the
server together, then reads the database.

One thing here fails on the old page: a station whose name has an
apostrophe ("Chef's Grill") was written into an onclick attribute, so its
button was a syntax error and could not be picked.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test kitchen.test_kitchen_browser
"""
from decimal import Decimal as D

from accounts.models import User
from core.tests.browser import BrowserTestCase
from kitchen.services.kot_service import create_kot
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from setup.models import KitchenStation
from tenants.models import Outlet, Tenant

BOARD = """
const card = (dish) => t.all(".order-card").find((c) => c.textContent.includes(dish));
const row = (dish) => t.all(".item-row").find((r) => r.textContent.includes(dish));
const tap = (dish, action) => {
  const button = row(dish).querySelector(`[data-action="${action}"]`);
  if (!button) throw new Error(`${dish} has no ${action} button`);
  button.click();
};
const dishes = () => t.all(".item-name").map((e) => e.textContent).sort();
await t.until(() => t.all(".order-card").length > 0);
"""


class KitchenDisplayTest(BrowserTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Salt & Pepper", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        make_user = lambda name, role: User.objects.create_user(  # noqa: E731
            username=name, password="pw", role=role, tenant=self.tenant, outlet=self.outlet)
        self.owner = make_user("kds_owner", "owner")
        self.chef = make_user("kds_chef", "chef")
        self.grill = KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name="Chef's Grill",
                                                   is_default=True, is_active=True)
        self.bar = KitchenStation.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar", is_active=True)
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        naan = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food, name="Butter Naan",
                                       price=D("50"), gst_percentage=D("0"), station=self.grill)
        soda = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food, name="Lime Soda",
                                       price=D("80"), gst_percentage=D("0"), station=self.bar)
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="Window")
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, table=table, status="open",
                                          source="dine_in", created_by=self.owner)
        add = lambda dish, notes="": OrderItem.objects.create(  # noqa: E731
            order=self.order, menu_item=dish, quantity=2, price=dish.price, gst_percentage=D("0"),
            total_price=dish.price * 2, status="pending", notes=notes)
        self.naan = add(naan, notes="<b>no butter</b>")
        self.soda = add(soda)
        create_kot(self.owner, self.order, print_on_create=False)

    def status(self, item):
        item.refresh_from_db()
        return item.status

    def run_clean(self, steps, user=None):
        out = self.run_page("/kitchen/", steps, user=user or self.chef)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]

    def test_a_dish_goes_from_sent_to_cooking_to_ready(self):
        self.assertEqual(self.status(self.naan), "sent")
        seen = self.run_clean([
            ("tickets", BOARD + 'return { cards: t.all(".order-card").length, dishes: dishes(), table: t.text(".table-name"),'
                                ' note: t.text(".item-note"), bold: t.all(".item-note b").length,'
                                ' clear: t.all(\'[data-action="kitchen.clear"]\').length };'),
            ("start", BOARD + 'tap("Butter Naan", "item.start");'
                              ' return await t.until(() => Boolean(row("Butter Naan").querySelector(\'[data-action="item.ready"]\')));'),
            ("ready", BOARD + 'tap("Butter Naan", "item.ready");'
                              ' return await t.until(() => Boolean(row("Butter Naan").querySelector(".badge-done")));'),
            ("bump gone", BOARD + 'return card("Butter Naan").querySelector(\'[data-action="kot.bump"]\') === null;'),
        ])
        self.assertEqual(seen["tickets"], {"cards": 2, "dishes": ["Butter Naan", "Lime Soda"], "table": "Window",
                                           "note": "<b>no butter</b>", "bold": 0, "clear": 0})
        self.assertTrue(seen["bump gone"], "a ticket with every dish ready still offers Bump")
        self.assertEqual(self.status(self.naan), "ready")
        self.assertEqual(self.status(self.soda), "sent")

    def test_bump_marks_a_ticket_being_cooked_ready(self):
        """Bump moves the dishes being cooked to ready (the server leaves a
        dish nobody has started where it is: kitchen.views.bump_kot)."""
        self.run_clean([
            ("start", BOARD + 'tap("Lime Soda", "item.start");'
                              ' return await t.until(() => Boolean(row("Lime Soda").querySelector(\'[data-action="item.ready"]\')));'),
            ("bump", BOARD + 'card("Lime Soda").querySelector(\'[data-action="kot.bump"]\').click();'
                             ' return await t.until(() => Boolean(row("Lime Soda").querySelector(".badge-done")));'),
        ])
        self.assertEqual(self.status(self.soda), "ready")
        self.assertEqual(self.status(self.naan), "sent")

    def test_a_station_with_an_apostrophe_in_its_name_can_be_picked(self):
        seen = self.run_clean([
            ("grill", BOARD + 't.all(".station-btn").find((b) => b.textContent === "Chef\'s Grill").click();'
                              ' await t.until(() => t.all(".order-card").length === 1);'
                              ' return { dishes: dishes(), active: t.text(".station-btn.active") };'),
            ("all", BOARD + 't.all(".station-btn")[0].click();'
                            ' await t.until(() => t.all(".order-card").length === 2);'
                            ' return t.text(".station-btn.active");'),
        ])
        self.assertEqual(seen["grill"], {"dishes": ["Butter Naan"], "active": "Chef's Grill"})
        self.assertEqual(seen["all"], "All Stations")

    def test_the_owner_clears_the_screen_after_being_asked(self):
        seen = self.run_clean([
            ("ask", BOARD + 't.click(\'[data-action="kitchen.clear"]\');'
                            ' return await t.until(() => { const el = document.querySelector(".swal2-title"); return el && el.textContent; });'),
            ("still there", 'return t.all(".order-card").length;'),
            ("yes", 't.click(".swal2-confirm");'
                    ' await t.until(() => document.querySelector(".empty-state"));'
                    ' return t.text(".empty-state h3");'),
        ], user=self.owner)
        self.assertEqual(seen["ask"], "Clear every ticket off this screen?")
        self.assertEqual(seen["still there"], 2, "the tickets went before the owner answered")
        self.assertEqual(seen["yes"], "All Caught Up")
        self.assertEqual(self.status(self.naan), "served")
        self.assertEqual(self.status(self.soda), "served")

    def test_the_kitchen_is_dark_and_its_light_switch_is_remembered(self):
        look = ('const root = document.documentElement.classList;'
                ' return { dark: root.contains("dark"), light: root.contains("kitchen-light"),'
                ' icon: document.getElementById("themeIcon").className,'
                ' page: getComputedStyle(document.body).backgroundColor };')
        seen = self.run_clean([
            ("at first", BOARD + look),
            # The theme fades the page's colour over 0.3 seconds: read it once the fade is over.
            ("switched", 't.click(\'[data-action="kitchen.light"]\');'
                         ' await t.until(() => document.getAnimations().length === 0'
                         ' && getComputedStyle(document.body).backgroundColor !== "rgb(0, 0, 0)");' + look),
            ("reload", "location.reload();", {"selector": ".order-card"}),
            ("after reload", look),
        ])
        self.assertEqual(seen["at first"], {"dark": True, "light": False, "icon": "bi bi-sun", "page": "rgb(0, 0, 0)"})
        self.assertEqual(seen["switched"], {"dark": True, "light": True, "icon": "bi bi-moon", "page": "rgb(250, 250, 250)"})
        self.assertEqual(seen["after reload"], seen["switched"])
