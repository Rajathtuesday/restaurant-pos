"""
The guest QR menu in a real browser (phase 2, 13 Nov 2026).

The only Rasova screen a guest sees. Its 790 lines of inline script and
520 of inline style became modules (static/js/qr_menu/) and a stylesheet;
this drives the page as a guest on a phone and then reads the database:
browsing and filtering, a dish with a required option (said inside the
sheet, never by alert()), the cart and its total, placing the order and
a second round on the same bill, the status pill, calling a waiter,
clearing the cart (two taps, never confirm()), dark mode across a reload,
and a menu with no dishes yet.

Skipped when Playwright or its Chromium isn't installed.
Run: python manage.py test menu.tests.test_qr_menu_browser
"""
from decimal import Decimal as D

from core.tests.browser import BrowserTestCase
from menu.models import MenuCategory, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup
from orders.models import Order, OrderRequest, Table
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant
from waiter.models import WaiterCall

PHONE = {"width": 390, "height": 844, "mobile": True}
HELP = """
const card = (name) => t.all(".item-card").find((c) => c.dataset.name === name);
const add = (name) => card(name).querySelector('[data-action="dish.add"]').click();
const toast = (word) => t.until(() => { const b = document.getElementById("miniToast");
  return b.classList.contains("show") && b.textContent.includes(word) && b.textContent.trim(); });
const shown = (id) => document.getElementById(id).classList.contains("active");
"""


class QrMenuBrowserTest(BrowserTestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Spice Garden", tenant_type="fine_dining")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no=SAMPLE_GSTIN, gst_inclusive=False)
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        make = lambda name, price, veg=True: MenuItem.objects.create(  # noqa: E731
            tenant=self.tenant, outlet=self.outlet, category=food, name=name, price=D(price),
            gst_percentage=D("5"), is_veg=veg)
        self.naan = make("Butter Naan", "50")
        self.tikka = make("Paneer Tikka", "250")
        self.chicken = make("Chilli Chicken", "300", veg=False)
        spice = ModifierGroup.objects.create(tenant=self.tenant, outlet=self.outlet, name="Spice", is_required=True,
                                             max_select=1)
        Modifier.objects.create(group=spice, name="Mild", price=D("0"))
        self.hot = Modifier.objects.create(group=spice, name="Hot", price=D("10"))
        MenuItemModifierGroup.objects.create(menu_item=self.tikka, modifier_group=spice)
        self.url = f"/menu/digital-menu/?table_token={self.table.qr_token}"

    def guest(self, steps, url=None):
        out = self.run_page(url or self.url, steps, viewport=PHONE)
        self.assertEqual(out["errors"], [], "the browser reported errors:\n" + "\n".join(out["errors"]))
        return out["results"]

    def test_a_guest_orders_with_a_required_option_then_a_second_round(self):
        seen = self.guest([
            ("browse", HELP + '''
                add("Butter Naan");
                add("Paneer Tikka");
                await t.until(() => shown("modifierModal"));
                t.click('[data-action="modifier.confirm"]');
                const said = t.text("#modifierError");
                const stillOpen = shown("modifierModal");
                t.all("#modifierGroupsContainer .modifier-option").find((o) => o.textContent.includes("Hot")).click();
                const total = t.text("#modTotalAmt");
                t.click('[data-action="modifier.confirm"]');
                await t.until(() => !shown("modifierModal"));
                return { said, stillOpen, total, items: t.text("#totalItems"), bar: t.text("#totalPrice") };'''),
            ("cart", HELP + '''
                t.click('[data-action="cart.open"]');
                await t.until(() => shown("cartModal"));
                return { rows: t.all("#cartItemsContainer .cart-item-name").map((e) => e.textContent),
                         options: t.text("#cartItemsContainer .cart-modifier-names"),
                         subtotal: t.text("#billSubtotal"), gst: t.text("#billGst"), total: t.text("#billTotal") };'''),
            ("place", HELP + '''
                t.click('[data-action="order.place"]');
                const said = await toast("confirm");
                await t.until(() => t.text("#totalItems") === "0");          // emptied only once the server took it
                await t.until(() => !document.getElementById("orderStatusPill").classList.contains("hidden"));
                return { said, bar: document.getElementById("cartBar").classList.contains("visible"),
                         pill: t.text("#orderStatusPillLabel") };'''),
            ("again", HELP + '''
                add("Butter Naan");
                t.click('[data-action="cart.open"]');
                await t.until(() => shown("cartModal"));
                const already = t.text("#cartItemsContainer");
                t.click('[data-action="order.place"]');
                // Not the toast: the first order's may still be showing.
                await t.until(() => t.text("#totalItems") === "0");
                return already;'''),
        ])
        self.assertEqual(seen["browse"]["said"], 'Please choose an option for "Spice".')
        self.assertTrue(seen["browse"]["stillOpen"])
        self.assertEqual(seen["browse"]["total"], "₹260")
        self.assertEqual((seen["browse"]["items"], seen["browse"]["bar"]), ("2", "₹310"))
        self.assertEqual(seen["cart"]["rows"], ["Butter Naan", "Paneer Tikka"])
        self.assertEqual(seen["cart"]["options"], "Hot")
        # 50 + 260 at 5% GST on top: 310 + 15.50
        self.assertEqual((seen["cart"]["subtotal"], seen["cart"]["gst"]), ("₹310", "₹15.50"))
        # Until 27 Nov 2026 the guest read "Order sent to the kitchen" while
        # every dish waited for a waiter's yes. The words say what happens.
        self.assertEqual(seen["place"]["said"].strip(), "Order received: waiting for the restaurant to confirm")
        self.assertFalse(seen["place"]["bar"])
        self.assertEqual(seen["place"]["pill"], "Received")
        self.assertIn("Already ordered", seen["again"])

        order = Order.objects.get(tenant=self.tenant)
        self.assertEqual(order.table, self.table)
        self.assertEqual(sorted((i.menu_item.name, i.quantity) for i in order.items.all()),
                         [("Butter Naan", 1), ("Butter Naan", 1), ("Paneer Tikka", 1)])
        tikka = order.items.get(menu_item=self.tikka)
        self.assertEqual([m.modifier_id for m in tikka.modifiers.all()], [self.hot.id])
        self.assertEqual(OrderRequest.objects.filter(order=order).count(), 2, "each round had its own request id")
        # What the toast says is what happened: every dish waits for a waiter.
        self.assertEqual({i.status for i in order.items.all()}, {"review"})

    def test_filters_and_search(self):
        seen = self.guest([("filter", HELP + '''
            const visible = () => t.all(".item-card").filter((c) => c.offsetParent !== null).map((c) => c.dataset.name);
            t.click('[data-filter="nonveg"]');
            const nonveg = visible();
            t.click('[data-filter="all"]');
            const box = document.getElementById("searchInput");
            box.value = "naan";
            box.dispatchEvent(new Event("input", { bubbles: true }));
            return { nonveg, search: visible() };''')])
        self.assertEqual(seen["filter"], {"nonveg": ["Chilli Chicken"], "search": ["Butter Naan"]})

    def test_calling_a_waiter(self):
        seen = self.guest([("call", HELP + '''
            t.click('[data-action="waiter.call"]');
            await toast("waiter");
            return t.text("#callWaiterLabel");''')])
        self.assertEqual(seen["call"], "Notified")
        self.assertTrue(WaiterCall.objects.filter(table=self.table, is_resolved=False).exists())

    def test_clearing_the_cart_takes_two_taps(self):
        seen = self.guest([("clear", HELP + '''
            add("Butter Naan");
            t.click('[data-action="cart.open"]');
            await t.until(() => shown("cartModal"));
            t.click('[data-action="cart.clear"]');
            const asked = t.text("#clearCartLabel");
            const kept = t.text("#totalItems");
            t.click('[data-action="cart.clear"]');
            return { asked, kept, after: t.text("#totalItems"),
                     bar: document.getElementById("cartBar").classList.contains("visible") };''')])
        self.assertEqual(seen["clear"], {"asked": "Clear?", "kept": "1", "after": "0", "bar": False})

    def test_a_cart_line_with_an_offer_is_tidy(self):
        """The walkthrough recording (23 Nov 2026): the quantity pill stretched
        to the width of the offer line, the line said the price before the
        offer, and the bar said "1 ITEMS"."""
        from offers.models import Offer, OfferTarget
        from tenants.models import TenantFeatureOverride

        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="offers", enabled=True)
        offer = Offer.objects.create(tenant=self.tenant, outlet=self.outlet, kind="percent_off", percent=D("20"),
                                     name="Happy hour: 20% off the breads, every evening this month")
        OfferTarget.objects.create(offer=offer, menu_item=self.naan)
        seen = self.guest([("cart", HELP + '''
            add("Butter Naan");
            t.click('[data-action="cart.open"]');
            await t.until(() => shown("cartModal") && document.querySelector(".cart-offer"));
            const pill = document.querySelector(".cart-qty").getBoundingClientRect().width;
            const offer = document.querySelector(".cart-offer").getBoundingClientRect().width;
            return { pill: Math.round(pill), offer: Math.round(offer), was: t.text(".cart-was"),
                     price: t.text(".cart-item-price"), bar: t.text(".cart-items-text") };''')])
        cart = seen["cart"]
        self.assertLess(cart["pill"], 150, cart)
        self.assertGreater(cart["offer"], cart["pill"], "the offer line is the wide one, not the pill")
        self.assertEqual(cart["was"], "₹50")
        self.assertIn("₹40", cart["price"])
        self.assertRegex(cart["bar"], r"(?i)^1 item$")

    def test_dark_mode_holds_across_a_reload(self):
        seen = self.guest([
            ("toggle", 't.click(\'[data-action="theme.toggle"]\'); return document.documentElement.classList.contains("dark");'),
            ("reload", "location.reload();", {"selector": ".item-card"}),
            ("after", 'return { dark: document.documentElement.classList.contains("dark"), icon: document.getElementById("themeIcon").className };'),
        ])
        self.assertTrue(seen["toggle"])
        self.assertEqual(seen["after"], {"dark": True, "icon": "bi bi-sun"})

    def test_every_field_is_readable_in_dark_mode(self):
        # Why (26 Nov 2026, item 5 of the fault plan): the special
        # instructions box and the search box set a dark background but no
        # text colour, so a guest typing "less spicy" saw black on #111
        # (1.11:1). Each field, with what a guest typed, against its own
        # background: 4.5:1 at least, in light and in dark.
        contrast = HELP + '''
            const rgb = (s) => s.match(/[\\d.]+/g).slice(0, 3).map(Number);
            const lum = (c) => { const [r, g, b] = c.map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
                                 return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
            const ratio = (el) => { const cs = getComputedStyle(el);
                                    const [a, b] = [lum(rgb(cs.color)), lum(rgb(cs.backgroundColor))].sort((x, y) => y - x);
                                    return Math.round(((a + 0.05) / (b + 0.05)) * 100) / 100; };
            add("Paneer Tikka");
            await t.until(() => shown("modifierModal"));
            const box = document.getElementById("specialInstructions");
            box.value = "less spicy";
            const search = document.getElementById("searchInput");
            search.value = "naan";
            return { instructions: ratio(box), search: ratio(search) };
        '''
        light = self.guest([("seen", contrast)])["seen"]
        dark = self.guest([
            ("toggle", 't.click(\'[data-action="theme.toggle"]\'); return true;'),
            ("seen", contrast),
        ])["seen"]
        for mode, seen in (("light", light), ("dark", dark)):
            for field, value in seen.items():
                self.assertGreaterEqual(value, 4.5, f"{field} in {mode} mode: {seen}")

    def test_a_menu_with_no_dishes_says_so(self):
        empty_outlet = Outlet.objects.create(tenant=self.tenant, name="New Branch")
        table = Table.objects.create(tenant=self.tenant, outlet=empty_outlet, name="B1")
        seen = self.guest([("empty", 'return { title: t.text("[data-empty-state] h2"), cards: t.all(".item-card").length };')],
                          url=f"/menu/digital-menu/?table_token={table.qr_token}")
        self.assertEqual(seen["empty"], {"title": "The menu is being set up", "cards": 0})
