"""
The QR menu's cart, run for real: the page is rendered by Django, its scripts
run in Node against a small stand-in for the browser page, and a guest taps
ADD and then VIEW CART through the page's own onclick handlers.

Why: from 28 Sep to 3 Oct 2026 VIEW CART did nothing on every QR and digital
menu. The cart started reading each dish's tax from a DISH_TAX table that the
page never defined, so opening the cart threw a ReferenceError before the
sheet appeared. The other cart tests only read the page's HTML, so nothing
ran the script that broke. This one does.

Needs Node.js, like test_cart_tax_js (skipped locally without it, a failure
in CI).

Run: python manage.py test menu.tests.test_qr_cart_js
"""
import html
import json
import re
import subprocess
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings

from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from orders.tests.test_cart_tax_js import NODE, _needs_node
from orders.tests.test_cart_wiring import CartWiringBase
from tenants.models import TenantFeatureOverride

CART_TAX = Path(settings.BASE_DIR) / "static" / "js" / "cart_tax.js"

# A browser page, as far as the QR menu's script touches one: elements by id
# (only ids the rendered HTML really has, so a missing element is null as in
# a browser), class lists, a localStorage, and timers and fetch that never
# fire. Every step's error is reported, not thrown.
HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
const page = JSON.parse(fs.readFileSync(0, "utf8"));

class ClassList {
  constructor() { this.names = new Set(); }
  add(...n) { n.forEach(x => this.names.add(x)); }
  remove(...n) { n.forEach(x => this.names.delete(x)); }
  toggle(n, force) {
    const on = force === undefined ? !this.names.has(n) : !!force;
    on ? this.names.add(n) : this.names.delete(n);
    return on;
  }
  contains(n) { return this.names.has(n); }
}

class Element {
  constructor(id) {
    this.id = id || ""; this.classList = new ClassList(); this.style = {}; this.dataset = {};
    this.children = []; this.innerText = ""; this.textContent = ""; this.value = ""; this._html = "";
    this.attributes = {};
  }
  get innerHTML() { return this._html; }
  set innerHTML(value) { this._html = String(value); if (value === "") this.children = []; }
  get className() { return [...this.classList.names].join(" "); }
  set className(value) {
    this.classList = new ClassList();
    String(value).split(/\s+/).filter(Boolean).forEach(n => this.classList.add(n));
  }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener() {} removeEventListener() {}
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  removeAttribute(k) { delete this.attributes[k]; }
  querySelector() { return null; } querySelectorAll() { return []; }
  closest() { return null; } contains() { return false; }
  focus() {} blur() {} remove() {} scrollIntoView() {}
}

const known = new Set(page.ids);
const elements = new Map();
const loaded = [];
const document = {
  body: new Element("body"), documentElement: new Element("html"),
  getElementById(id) {
    if (!known.has(id)) return null;
    if (!elements.has(id)) {
      const el = new Element(id);
      if (id in page.json) el.textContent = page.json[id];
      elements.set(id, el);
    }
    return elements.get(id);
  },
  createElement() { return new Element(); },
  querySelector() { return null }, querySelectorAll() { return []; },
  addEventListener(type, fn) { if (type === "DOMContentLoaded") loaded.push(fn); },
  removeEventListener() {},
};
const store = new Map();
const storage = {
  getItem: k => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
};
const never = () => new Promise(() => {});
const sandbox = {
  document, console, localStorage: storage, sessionStorage: storage,
  fetch: never, setTimeout: () => 0, setInterval: () => 0, clearTimeout() {}, clearInterval() {},
  requestAnimationFrame: () => 0, confirm: () => true, alert() {},
  navigator: { userAgent: "node", vibrate() {} },
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  location: { href: "", pathname: "", search: "", hash: "" }, history: { replaceState() {} },
  URLSearchParams, addEventListener() {}, removeEventListener() {}, scrollTo() {},
};
sandbox.window = sandbox;
const context = vm.createContext(sandbox);

const errors = [];
function run(label, code) {
  try { vm.runInContext(code, context, { filename: label }); }
  catch (e) { errors.push(`${label}: ${e && e.name}: ${e && e.message}`); }
}
run("cart_tax.js", fs.readFileSync(page.cartTax, "utf8"));
page.scripts.forEach((code, i) => run(`inline script ${i + 1}`, code));
loaded.forEach((fn, i) => { try { fn(); } catch (e) { errors.push(`DOMContentLoaded ${i}: ${e.message}`); } });
page.taps.forEach(([label, code]) => run(label, code));

const el = id => elements.get(id) || document.getElementById(id);
process.stdout.write(JSON.stringify({
  errors,
  cartOpen: !!el("cartModal") && el("cartModal").classList.contains("active"),
  overlayOpen: !!el("cartOverlay") && el("cartOverlay").classList.contains("active"),
  rows: el("cartItemsContainer") ? el("cartItemsContainer").children.length : null,
  subtotal: el("billSubtotal") && el("billSubtotal").innerText,
  gst: el("billGst") && el("billGst").innerText,
  vat: el("billVat") && el("billVat").innerText,
  vatShown: !!el("billVatRow") && el("billVatRow").style.display !== "none",
  total: el("billTotal") && el("billTotal").innerText,
}));
"""


class QrCartRunsTest(CartWiringBase):
    """A registered outlet, prices without GST: Curd (30, 0%) from the base,
    and Paneer Tikka (200, 5%)."""

    def setUp(self):
        super().setUp()
        category = MenuCategory.objects.get(tenant=self.tenant, name="Food")
        self.paneer = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                              name="Paneer Tikka", price=D("200"), gst_percentage=D("5"))

    def tap_through(self, *dishes):
        """Render the QR menu, tap ADD on each dish, then VIEW CART."""
        _needs_node(self)
        page = self.qr_menu()

        def onclick(pattern):
            match = re.search(pattern + r'[^>]*onclick="([^"]*)"', page)
            self.assertIsNotNone(match, f"no button matching {pattern}")
            return html.unescape(match.group(1))

        taps = [(f"ADD {dish.name}", onclick(rf'id="addBtn-{dish.id}"')) for dish in dishes]
        taps.append(("VIEW CART", onclick(r'class="view-cart-btn"')))
        done = subprocess.run(
            [NODE, "-e", HARNESS], capture_output=True, text=True, timeout=60, check=True,
            input=json.dumps({
                "cartTax": str(CART_TAX),
                "ids": re.findall(r'\sid="([^"]+)"', page),
                "json": dict(re.findall(r'<script id="([^"]+)" type="application/json">(.*?)</script>', page, re.S)),
                "scripts": re.findall(r"<script>(.*?)</script>", page, re.S),
                "taps": taps,
            }),
        )
        return json.loads(done.stdout)

    def test_view_cart_opens_the_cart_with_its_dishes_and_total(self):
        cart = self.tap_through(self.paneer, self.curd)
        self.assertEqual(cart["errors"], [])
        self.assertTrue(cart["cartOpen"], "VIEW CART did not open the cart")
        self.assertTrue(cart["overlayOpen"])
        self.assertEqual(cart["rows"], 2)
        # 5% on the paneer only: the curd is 0%
        self.assertEqual((cart["subtotal"], cart["gst"], cart["total"]), ("230.00", "10.00", "240.00"))


class QrPubCartRunsTest(QrCartRunsTest):
    """With the liquor_vat feature: the rum's 5.5% VAT can only come from the
    page's dish tax table (its GST rate is 0), so this proves the cart reads it."""

    def setUp(self):
        super().setUp()
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        self.rum = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=bar, name="Rum", price=D("220"),
            gst_percentage=D("0"), vat_class=add_liquor_class(self.outlet, "Spirits", rate="5.5"),
        )

    def test_the_cart_shows_the_liquor_vat(self):
        cart = self.tap_through(self.rum)
        self.assertEqual(cart["errors"], [])
        self.assertTrue(cart["cartOpen"])
        self.assertEqual((cart["subtotal"], cart["vat"], cart["total"]), ("220.00", "12.10", "232.10"))
        self.assertTrue(cart["vatShown"])

