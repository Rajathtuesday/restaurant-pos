"""
The billing screen in a real browser: the page Django renders, the modules
in static/js/billing/, Bootstrap and the server, with a cashier's taps.

Why: on 12 November 2026 the screen's 800 lines of inline script became
modules. The module tests (static/js/tests/billing.test.mjs) prove each
piece with fakes, and the page tests prove the markup, but a wrong id, a
button without its data-action or a module that throws while loading would
pass both and leave a cashier with a dead screen. Here Chrome loads the
page from a live server, taps through an order, and the test then reads
the order from the database.

Playwright and Chromium, like the other browser tests
(core/tests/browser.py); skipped where they are not installed.

Run: python manage.py test orders.tests.test_billing_browser
"""
from decimal import Decimal as D


from django.utils import timezone

from accounts.models import User
from core.tests.browser import BrowserTestCase
from core.utils import get_business_date
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from setup.models import PaymentConfig
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant, TenantFeatureOverride
from tokens.models import TokenOrder

# Tap a dish, wait for its sheet (and the modifiers call to answer), add it,
# and wait for the sheet to be gone again before the next tap.
ADD_DISH = """
async function addDish(id) {
  t.click('[data-action="menu.pick"][data-id="' + id + '"]');
  await t.until(() => document.querySelector("#itemModal.show")
    && !t.text("#modifierContainer").includes("Checking"));
  t.click('[data-action="menu.add"]');
  await t.until(() => !document.querySelector("#itemModal.show") && !document.querySelector(".modal-backdrop"));
}
"""
TOTALS = """
function totals(root) {
  const box = document.querySelector(root);
  const text = (sel) => { const el = box.querySelector(sel); return el ? el.textContent.trim() : null; };
  return { subtotal: text(".view-subtotal"), gst: text(".view-gst"), total: text(".view-total") };
}
"""


class BillingBrowserBase(BrowserTestCase):
    kitchen_display = True

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Browser Test", tenant_type="cafe")
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="kitchen_display",
                                             enabled=self.kitchen_display)
        self.outlet = Outlet.objects.create(
            tenant=self.tenant, name="Main", gst_no=SAMPLE_GSTIN, gst_inclusive=False,
            parcel_charge_amount=D("10"), parcel_gst_rate=D("18"),
        )
        PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet, cash_enabled=True)
        self.owner = User.objects.create_user(username="browser_owner", password="x",
                                              tenant=self.tenant, outlet=self.outlet, role="owner")
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.curd = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                            name="Curd", price=D("30"), gst_percentage=D("0"))
        self.paneer = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=food,
                                              name="Paneer Tikka", price=D("200"), gst_percentage=D("5"))
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")

    def browse(self, steps, url="/billing/", viewport=None, allow=()):
        out = self.run_page(url, steps, user=self.owner, viewport=viewport)
        errors = [e for e in out["errors"] if not any(ok in e for ok in allow)]
        self.assertEqual(errors, [], "the browser reported errors:\n" + "\n".join(errors))
        return out["results"]


class DineInTest(BillingBrowserBase):
    """A restaurant with a kitchen screen: the Dispatch button."""

    def test_an_order_from_the_menu_to_the_kitchen(self):
        seen = self.browse([
            ("empty", """
                await t.until(() => t.text("#cartItemsContainerDesktop").includes("Empty Order"));
                return { label: t.text("#orderSummaryLabel"), active: t.text(".category-sidebar .category-item.active") };
            """),
            ("cart", ADD_DISH + TOTALS + f"""
                await addDish({self.paneer.id});
                await addDish({self.curd.id});
                await addDish({self.paneer.id});
                t.click('#cartItemsContainerDesktop [data-action="cart.inc"][data-index="1"]');
                t.click('#cartItemsContainerDesktop [data-action="cart.dec"][data-index="1"]');
                return {{
                  rows: t.all("#cartItemsContainerDesktop .cart-row").map((r) => r.querySelector(".cart-item-name").textContent.trim()
                    + " x" + r.querySelector(".qty-value").textContent),
                  ...totals(".context-sidebar"),
                }};
            """),
            ("table", f"""
                const select = document.getElementById("tableSelectDesktop");
                select.value = "{self.table.id}";
                select.dispatchEvent(new Event("change", {{ bubbles: true }}));
                await t.until(() => t.text("#runningItemsDesktop").includes("No active services"));
                return t.text("#orderSummaryLabel");
            """),
            ("dispatch", """
                t.click('.context-sidebar [data-action="order.submit"]');
                await t.until(() => t.text("#runningItemsDesktop").includes("Paneer Tikka"));
                return {
                  cart: t.text("#cartItemsContainerDesktop"),
                  total: t.text(".context-sidebar .view-total"),
                  running: t.all("#runningItemsDesktop .running-name").map((e) => e.textContent.trim()),
                  cancel: !!document.querySelector('#runningItemsDesktop [data-action="order.cancel"]'),
                  button: t.text('.context-sidebar [data-action="order.submit"]'),
                };
            """),
            ("tabs", """
                t.click('#tabRunning');
                const running = !document.getElementById("runningView").classList.contains("d-none");
                t.click('#tabCart');
                return running && !document.getElementById("cartView").classList.contains("d-none");
            """),
        ])
        self.assertEqual(seen["empty"], {"label": "Walk-in · Dine-in", "active": "Food"})
        # 2 x 200 at 5% and a curd at 0%: 430, GST 20, the guest pays 450
        self.assertEqual(seen["cart"], {"rows": ["Paneer Tikka x2", "Curd x1"],
                                        "subtotal": "₹430", "gst": "₹20.00", "total": "₹450"})
        self.assertEqual(seen["table"], "T1 · Dine-in")
        self.assertEqual(seen["dispatch"]["cart"], "Empty Order")
        self.assertEqual(seen["dispatch"]["total"], "₹0")
        self.assertCountEqual(seen["dispatch"]["running"], ["2 × Paneer Tikka", "1 × Curd"])
        self.assertTrue(seen["dispatch"]["cancel"])
        self.assertEqual(seen["dispatch"]["button"], "Dispatch")
        self.assertTrue(seen["tabs"])

        order = Order.objects.get(tenant=self.tenant, table=self.table)
        self.assertEqual(sorted((i.menu_item.name, i.quantity) for i in order.items.all()),
                         [("Curd", 1), ("Paneer Tikka", 2)])

    def test_on_a_phone_the_cart_is_its_own_page(self):
        seen = self.browse([
            ("phone", ADD_DISH + f"""
                await addDish({self.curd.id});
                const fab = document.querySelector(".fab-cart");
                const shown = getComputedStyle(fab).display !== "none";
                fab.click();
                const page = document.getElementById("mobileCartPage");
                const open = page.classList.contains("show");
                const rows = t.all("#cartItemsContainerMobile .cart-row").length;
                t.click('[data-action="mobile.close"]');
                return {{ shown, open, rows, closed: !page.classList.contains("show"), fab: fab.textContent.replace(/\\s+/g, " ").trim() }};
            """),
        ], viewport={"width": 390, "height": 800, "mobile": True})
        self.assertEqual(seen["phone"], {"shown": True, "open": True, "rows": 1, "closed": True,
                                         "fab": "View Order (₹30)"})


class CounterTest(BillingBrowserBase):
    """No kitchen screen and no kitchen printer: pay and print the slip in one go."""
    kitchen_display = False

    def test_pay_and_print_with_a_parcel_charge(self):
        seen = self.browse([
            ("cart", ADD_DISH + TOTALS + f"""
                await addDish({self.paneer.id});
                await addDish({self.curd.id});
                const before = totals(".context-sidebar");
                t.click(".context-sidebar .parcel-btn");
                return {{ before, after: totals(".context-sidebar"), parcel: t.text(".context-sidebar .parcel-btn-text"),
                          row: t.text(".context-sidebar .parcel-row span:last-child") }};
            """),
            ("sheet", """
                t.click('.context-sidebar [data-action="order.submit"]');
                await t.until(() => document.querySelector("#qsrPayModal.show"));
                return { total: t.text("#qsrGrandTotal"), payDisabled: document.getElementById("qsrPayBtn").disabled };
            """),
            ("pay", """
                t.click('[data-action="qsr.method"][data-method="cash"]');
                const amount = document.getElementById("qsrAmount");
                amount.value = "300";
                amount.dispatchEvent(new Event("input", { bubbles: true }));
                const change = t.text("#qsrChangeLine");
                t.click('[data-action="qsr.pay"]');
                await t.until(() => getComputedStyle(document.getElementById("qsrConfirm")).display === "flex", 15000);
                const paid = t.text("#qsrChangeDue");
                await t.until(() => t.text("#cartItemsContainerDesktop").includes("Empty Order"), 8000);
                return { change, paid, hidden: getComputedStyle(document.getElementById("qsrConfirm")).display };
            """),
        ], allow=(
            # No print agent runs beside a test: the page tries it, then falls
            # back to the receipt page, which a headless browser will not pop up.
            "ws://localhost:8765",
        ))
        # 230 of food, GST 10. The outlet charges 10 a parcel on each of the 2
        # items, with 18% GST on it: 20 + 3.60. The guest pays 263.60, rounded 264.
        # (The cart used to show one flat 10 and a total of 252 here, then the
        # payment sheet asked for 264.)
        self.assertEqual(seen["cart"]["before"], {"subtotal": "₹230", "gst": "₹10.00", "total": "₹240"})
        self.assertEqual(seen["cart"]["after"], {"subtotal": "₹230", "gst": "₹13.60", "total": "₹264"})
        self.assertEqual(seen["cart"]["row"], "₹20")
        self.assertEqual(seen["cart"]["parcel"], "Parcel Added ✓ (tap to remove)")
        # the server's own total for the order, the same as the cart showed
        self.assertEqual(seen["sheet"], {"total": "₹ 264.00", "payDisabled": True})
        self.assertEqual(seen["pay"], {"change": "Change due: ₹36.00", "paid": "Change: ₹36.00", "hidden": "none"})

        order = Order.objects.get(tenant=self.tenant)
        self.assertEqual(order.status, "closed")        # paid in full at the counter
        self.assertEqual(order.grand_total, D("264.00"))


class TokenCounterTest(BillingBrowserBase):
    """The token counter: one tap adds a dish, Checkout goes to the bill."""

    def token_order(self, number):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                     source="counter", status="open")
        token = TokenOrder.objects.create(tenant=self.tenant, outlet=self.outlet, order=order, token_number=number,
                                          date=get_business_date(timezone.now(), self.outlet))
        return order, token

    def test_taps_a_parcel_and_checkout(self):
        order, token = self.token_order(1)
        seen = self.browse([
            ("loaded", """
                await t.until(() => t.text("#runningItemsDesktop").includes("No active items"));
                return { token: t.text("#orderTokenDisplay"), cart: t.text("#cartItemsContainerDesktop") };
            """),
            ("taps", TOTALS + f"""
                const curd = '.menu-card[data-id="{self.curd.id}"]';
                const paneer = '.menu-card[data-id="{self.paneer.id}"]';
                t.click(curd); t.click(curd); t.click(paneer);
                return {{
                  sheet: !!document.querySelector("#itemModal.show"),
                  badges: [t.text(curd + " .cart-badge"), t.text(paneer + " .cart-badge")],
                  marked: t.all(".menu-card.in-cart").length,
                  rows: t.all("#cartItemsContainerDesktop .cart-row").map((r) => r.querySelector(".cart-item-name").textContent.trim()
                    + " x" + r.querySelector(".qty-value").textContent),
                  ...totals(".context-sidebar"),
                }};
            """),
            ("parcel", TOTALS + """
                const offer = t.text(".context-sidebar .parcel-btn-text");
                t.click(".context-sidebar .parcel-btn");
                await t.until(() => document.querySelector(".context-sidebar .parcel-btn.is-on"));
                // the running order is polled every 5 seconds: the wish must outlive a poll
                await new Promise((r) => setTimeout(r, 5600));
                return { offer, on: !!document.querySelector(".context-sidebar .parcel-btn.is-on"), ...totals(".context-sidebar") };
            """),
            ("checkout", """
                t.click('.context-sidebar [data-action="order.submit"]');
                return true;
            """, f"/bill/{order.id}/"),
            ("bill", "return location.pathname;"),
        ], url=f"/token/{order.id}/bill/")
        self.assertEqual(seen["loaded"], {"token": token.display_number, "cart": "Empty Order"})
        self.assertEqual(seen["taps"], {
            "sheet": False, "badges": ["2", "1"], "marked": 2, "rows": ["Curd x2", "Paneer Tikka x1"],
            "subtotal": "₹260", "gst": "₹10.00", "total": "₹270",
        })
        # 10 a parcel on each of the 3 items, 18% GST on it: 30 + 5.40
        self.assertEqual(seen["parcel"], {"offer": "Add Parcel ₹10/item", "on": True,
                                          "subtotal": "₹260", "gst": "₹15.40", "total": "₹305"})
        self.assertEqual(seen["bill"], f"/bill/{order.id}/")

        order.refresh_from_db()
        self.assertEqual(order.status, "billing")
        self.assertEqual(sorted((i.menu_item.name, i.quantity) for i in order.items.all()),
                         [("Curd", 2), ("Paneer Tikka", 1)])
        # The wish made on the empty token reached the bill. (On the old page
        # the button did nothing here: the server charges by the items, and
        # the token had none yet.)
        self.assertEqual(order.parcel_surcharge, D("30.00"))
        self.assertEqual(order.grand_total, D("305.00"))

    def test_switching_to_another_token_without_reloading(self):
        first, _ = self.token_order(1)
        second, second_token = self.token_order(2)
        seen = self.browse([
            ("switch", f"""
                window.stillHere = true;
                t.click('.menu-card[data-id="{self.curd.id}"]');
                t.click("#tabActive");
                const listed = t.all("#activeOrdersView .active-order-item").length;
                const summaryHidden = document.getElementById("cartSummaryBlock").classList.contains("d-none");
                t.click('#activeOrdersView .active-order-item[data-order-id="{second.id}"]');
                await t.until(() => t.text("#orderTokenDisplay") === "{second_token.display_number}");
                return {{
                  listed, summaryHidden,
                  path: location.pathname,
                  samePage: window.stillHere === true,
                  cartTab: document.getElementById("tabCart").classList.contains("active"),
                  cart: t.text("#cartItemsContainerDesktop"),
                  current: t.all("#activeOrdersView .active-order-item.current").map((a) => a.dataset.orderId),
                }};
            """),
        ], url=f"/token/{first.id}/bill/")
        self.assertEqual(seen["switch"], {
            "listed": 2, "summaryHidden": True, "path": f"/token/{second.id}/bill/", "samePage": True,
            "cartTab": True, "cart": "Empty Order", "current": [str(second.id)],
        })


class OfflineReadyTest(BillingBrowserBase):
    """The billing screen has to open with no network on a device that
    installed Rasova but has not opened billing yet. Its code is in files
    now, so the service worker must have cached every one of them."""

    def test_installing_the_app_caches_everything_the_billing_page_loads(self):
        seen = self.run_page("/dashboard/", [
            ("cached", """
                await navigator.serviceWorker.ready;
                const origin = location.origin;
                const html = await t.until(async () => {
                  const page = await caches.match("/billing/");
                  return page ? page.text() : null;
                }, 20000);
                // What the page loads, and what each of those loads in turn,
                // read from the cache alone: asking the server here would go
                // through the service worker and cache the file as it passed.
                const needed = new Set();
                const missing = [];
                const local = (ref, base) => {
                  const url = new URL(ref, base);
                  return url.origin === origin && url.pathname.startsWith("/static/") ? url.pathname : null;
                };
                async function walk(path) {
                  if (needed.has(path)) return;
                  needed.add(path);
                  const hit = await caches.match(path);
                  if (!hit) { missing.push(path); return; }
                  if (!path.endsWith(".js")) return;
                  const text = await hit.text();
                  for (const m of text.matchAll(/(?:\\bimport|\\bfrom)\\s*["'](\\.{1,2}\\/[^"']+)["']/g)) {
                    const next = local(m[1], origin + path);
                    if (next) await walk(next);
                  }
                }
                for (const m of html.matchAll(/<(?:script|link)\\b[^>]*?\\b(?:src|href)="([^"]+)"/g)) {
                  const path = local(m[1], origin + "/billing/");
                  if (path) await walk(path);
                }
                const list = [...needed];
                return {
                  missing,
                  count: list.length,
                  billingModule: list.includes("/static/js/billing/index.js"),
                  itsImports: list.includes("/static/js/billing/dispatch.js") && list.includes("/static/js/cart_tax.js"),
                  billingCss: list.includes("/static/css/billing.css"),
                  pageIsBilling: html.includes('id="page-data"'),
                };
            """),
        ], user=self.owner)
        self.assertEqual(seen["errors"], [])
        cached = seen["results"]["cached"]
        self.assertTrue(cached["pageIsBilling"], "the page kept as /billing/ is not the billing page")
        self.assertTrue(cached["billingModule"] and cached["itsImports"] and cached["billingCss"], cached)
        self.assertEqual(cached["missing"], [], "the billing page needs these and they are not cached")
        self.assertGreater(cached["count"], 25)
