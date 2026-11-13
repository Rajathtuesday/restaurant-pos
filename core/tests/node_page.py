"""
Runs a rendered page's inline scripts in Node, against a small stand-in for
the browser page, tapping through its onclick handlers. For pages that still
have inline scripts (the offer setup page); a page moved to modules is tested
in a real browser instead (core/tests/browser.py).

Lifted from menu/tests/test_qr_cart_js.py on 13 Nov 2026, when the QR menu,
the page it was written for, became modules and its test moved to Chromium
(menu/tests/test_qr_cart_browser.py).
"""
import re
from pathlib import Path

from django.conf import settings

STATIC_JS = Path(settings.BASE_DIR) / "static" / "js"

# A browser page, as far as the QR menu's script touches one: elements by id
# (only ids the rendered HTML really has, so a missing element is null as in
# a browser), class lists, a localStorage, and timers and fetch that never
# fire. Every step's error is reported, not thrown.
#
# The page loads two kinds of script and the harness keeps the browser's
# order: plain scripts run as the page is read, modules (cart_tax.js,
# offers.js) run after that and before DOMContentLoaded. So an inline
# script that reaches for a module before DOMContentLoaded fails here as it
# would on a phone.
NODE_ARGS = ["--input-type=module", "-e"]
HARNESS = r"""
import vm from "node:vm";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
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
// Modules run outside the sandbox: these two are how they reach the page.
globalThis.window = sandbox;
globalThis.document = document;

const errors = [];
function run(label, code) {
  try { vm.runInContext(code, context, { filename: label }); }
  catch (e) { errors.push(`${label}: ${e && e.name}: ${e && e.message}`); }
}
page.files.forEach(file => run(path.basename(file), fs.readFileSync(file, "utf8")));
page.scripts.forEach((code, i) => run(`inline script ${i + 1}`, code));
for (const file of page.modules) {
  try { await import(pathToFileURL(file).href); }
  catch (e) { errors.push(`${path.basename(file)}: ${e && e.name}: ${e && e.message}`); }
}
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
  offers: el("billOffers") && el("billOffers").innerText,
  offersShown: !!el("billOffersRow") && el("billOffersRow").style.display !== "none",
  offerNotes: (el("cartItemsContainer") ? el("cartItemsContainer").children : [])
    .map(row => (row.innerHTML || "").match(/class="cart-offer"[^>]*>([^<]*)/)).filter(Boolean).map(m => m[1].trim()),
}));
"""


def our_scripts(page, module):
    """Our own script files the page loads, in its order: the modules, or
    the plain ones. Read from the page, so a new one can't be missed here."""
    tags = re.findall(r'<script( type="module")? src="/static/js/([\w.]+)"', page)
    return [str(STATIC_JS / name) for kind, name in tags
            if bool(kind) == module and (STATIC_JS / name).exists()]
