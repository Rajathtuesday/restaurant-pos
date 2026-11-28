"""
The kitchen display in a real browser when it falls behind, and when it
is signed out.

Why (28 Nov 2026, Tuesdays, production log): a cook's phone on a slow link
kept a Ready button on a whisky that was already ready. Two more taps each
got "400: Invalid state constraint: Item is not 'preparing'." as a toast,
and the board stayed as it was. Separately, a signed-out tab went on
asking for data once a minute for hours, saying nothing.

Both fail on the old code: the stale tap is refused with a 400 (a console
error here) and the board is not asked for again; signed out, there was no
notice and the board went on asking.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test kitchen.test_kitchen_live_browser
"""
from decimal import Decimal as D

from accounts.models import User
from core.tests.browser import BrowserTestCase
from kitchen.models import KitchenMessage
from kitchen.services.kot_service import create_kot
from kitchen.test_kitchen_browser import BOARD
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from tenants.models import Outlet, Tenant

# Another screen (a second phone) does something, straight to the server.
ANOTHER_SCREEN = """
const elsewhere = (url) => fetch(url, { method: "POST", headers: {
  "X-CSRFToken": document.querySelector('meta[name="csrf-token"]').content } });
"""


class KitchenBehindAndSignedOutTest(BrowserTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Copper Tap Live", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.chef = User.objects.create_user(username="live_chef", password="pw", role="chef",
                                             tenant=self.tenant, outlet=self.outlet)
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        whisky = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=bar,
                                         name="Glenfiddich 12", price=D("650"), gst_percentage=D("0"))
        table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="Booth 3")
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, table=table, status="open",
                                          source="dine_in", created_by=self.chef)
        self.whisky = OrderItem.objects.create(order=self.order, menu_item=whisky, quantity=1, price=D("650"),
                                               gst_percentage=D("0"), total_price=D("650"), status="pending")
        create_kot(self.chef, self.order, print_on_create=False)

    def test_a_ready_tap_on_a_board_that_fell_behind_is_done_and_the_board_catches_up(self):
        out = self.run_page("/kitchen/", [
            ("cooking", BOARD + 'tap("Glenfiddich", "item.start");'
                                ' return await t.until(() => Boolean(row("Glenfiddich").querySelector(\'[data-action="item.ready"]\')));'),
            # A second phone marks it ready; this board still shows Ready
            # and the cook taps it.
            ("stale tap", BOARD + ANOTHER_SCREEN +
             f'const done = await elsewhere("/item-ready/{self.whisky.id}/");'
             ' const stale = Boolean(row("Glenfiddich").querySelector(\'[data-action="item.ready"]\'));'
             ' tap("Glenfiddich", "item.ready");'
             ' await t.until(() => Boolean(row("Glenfiddich").querySelector(".badge-done")));'
             ' return { other: done.status, stale, toast: document.querySelector(".swal2-icon-error") !== null };'),
        ], user=self.chef)
        self.assertEqual(out["errors"], [], "a refused tap shows as a 400 here:\n" + "\n".join(out["errors"]))
        self.assertEqual(out["results"]["stale tap"], {"other": 200, "stale": True, "toast": False})
        self.whisky.refresh_from_db()
        self.assertEqual(self.whisky.status, "ready")
        self.assertEqual(KitchenMessage.objects.filter(order=self.order).count(), 1,
                         "the waiter is told once, not once per tap")

    def test_signed_out_in_another_tab_the_board_says_so_and_stops_asking(self):
        out = self.run_page("/kitchen/", [
            ("notice hidden", BOARD + 'return document.querySelector("[data-signed-out]").hidden;'),
            # The other tab signs out: one sign-in covers every tab.
            ("signed out", 'await fetch("/logout/");'
                           ' await t.until(() => !document.querySelector("[data-signed-out]").hidden);'
                           ' const note = document.querySelector("[data-signed-out]");'
                           ' const shown = getComputedStyle(note);'
                           ' return { words: note.textContent.replace(/\\s+/g, " ").trim(),'
                           ' link: note.querySelector("a").getAttribute("href"),'
                           ' position: shown.position, display: shown.display };'),
            # Nothing more is asked: watch for longer than the board's
            # 5-second poll and the badges' 8-second one.
            ("then", 'const asked = []; const real = window.fetch;'
                     ' window.fetch = (...a) => { asked.push(String(a[0])); return real(...a); };'
                     ' await new Promise((r) => setTimeout(r, 8600));'
                     ' return { asked, tickets: t.all(".order-card").length };'),
        ], user=self.chef)
        unexpected = [e for e in out["errors"] if " 401 " not in e and "status of 401" not in e]
        self.assertEqual(unexpected, [], "\n".join(out["errors"]))
        seen = out["results"]
        self.assertIs(seen["notice hidden"], True)
        self.assertEqual(seen["signed out"]["words"], "You're signed out, so this screen has stopped updating. Sign in again")
        self.assertEqual(seen["signed out"]["link"], "/login/?next=/kitchen/")
        self.assertEqual((seen["signed out"]["position"], seen["signed out"]["display"]), ("fixed", "flex"))
        self.assertEqual(seen["then"]["asked"], [], "the board (5 s) and the badges (8 s) stopped asking")
        self.assertEqual(seen["then"]["tickets"], 1, "the last tickets stay under the notice")
