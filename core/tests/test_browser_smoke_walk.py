"""
The browser smoke walk: every page the owner of a fully set-up fine-dining
restaurant can open, opened in a real browser.

Two breaks sat in unpushed commits for a day in November 2026 because no
test opened the pages they broke: a fonts <link> that lost its ">" made
the QR menu print its stylesheet as text, and the running order page
called a function at the top level that read a module's global before the
module had run. Rendering a template cannot see either; a browser sees
both at once.

The URL list is the tenant walk's (core/tests/test_tenant_walk.py): every
named route, its ids filled from the restaurant's own rows. A route is a
page when it answers the owner's GET with 200 and HTML. Each page fails on:

  - an uncaught error, or a console.error (how Chromium reports a failed request)
  - a static file that did not load
  - stylesheet text among the words on the page
  - a header that is not the one header (base.html): exactly one brand
    link, one theme switch, one logout, none by onclick (V2, 13 Nov 2026)
  - a page block that does not use the width it is given (15 Nov 2026: the
    dashboard sat in a 565 px column on a 1400 px laptop, because a block
    centred with margin: 0 auto inside base.html's flex <main> shrinks to
    its content instead of growing to its max-width)
  - an em dash among the words on screen (the house rule, 26 Nov 2026)

The walk goes three times: a laptop (1400 px, light), a phone (390 px,
light) and a tablet (768 px, dark). On the phone and the tablet a page
also fails on words, a field or a button that runs past the edge of the
screen, whether the page scrolls sideways or something hides it. Until
26 Nov 2026 the walk only went at 1400 px, and a sweep by hand found a
dozen pages cut off on a phone or a tablet
(md_files/rasova_fault_fix_plan_2026-11-26.md).

A new page is walked the day its URL is added, with no change here.
"""
import re
from decimal import Decimal
from urllib.parse import urlparse

from django.conf import settings
from django.test import Client
from django.urls import get_resolver, reverse

from core.testing import freeze_ratelimit_clock
from core.utils import get_business_date
from core.tests.browser import BrowserTestCase, _console_error
from core.tests.test_tenant_walk import REGISTRY, Restaurant, _routes

# Routes that are not opened, each with its reason.
NOT_WALKED = {
    "logout": "ends the session the walk runs in",
    "live-demo": "logs in as the demo restaurant, replacing the owner",
    "demo_switch": "switches the session to demo mode",
}

# Parts of a page parked off screen on purpose until they are opened: the
# cut-off check does not count them. Each with its reason.
PARKED = {
    # (#toastBox, the till's toast, was parked here on 26 Nov 2026 as "out
    # of sight until it has something to say". It was not parked: rasova.css's
    # main > * { width: 100% } stretched it from -20 px across the whole
    # screen, and every "Order Sent" showed so. Never park a box to make the
    # walk pass without looking at why it is off the edge.)
    ".drawer:not(.open)": "a purchase order drawer (new, receive), slid off screen until opened",
    "#detailPanel": "Order History's detail panel, slid off screen until an order is opened",
    ".marquee-section": "the marketing page's ticker runs past the edges on purpose",
}

# Words, fields and buttons that run past the left or right edge of the
# screen. A row that scrolls sideways on purpose (overflow-x auto or scroll,
# and it does scroll) holds its own; anything else past the edge is cut off
# for the person holding the phone. Measured against the page's own width
# (clientWidth): on a phone Chrome widens innerWidth to fit wide content,
# which would hide the very overflow this looks for.
CUT_OFF = r"""
const W = document.documentElement.clientWidth;
const parked = PARKED_SELECTORS;
const found = [];
const name = (el) => {
  let s = el.tagName.toLowerCase();
  if (el.id) s += "#" + el.id;
  const cls = [...el.classList].slice(0, 2);
  if (cls.length) s += "." + cls.join(".");
  const t = (el.innerText || el.value || el.placeholder || el.getAttribute("aria-label") || "").trim().replace(/\s+/g, " ").slice(0, 40);
  return t ? `${s} "${t}"` : s;
};
const scrolls = (el) => {
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const ox = getComputedStyle(p).overflowX;
    if ((ox === "auto" || ox === "scroll") && p.scrollWidth > p.clientWidth) return true;
  }
  return false;
};
const unseen = (el) => {
  if (el.closest(parked)) return true;
  if (el.closest(".modal:not(.show), [hidden], .offcanvas:not(.show), dialog:not([open])")) return true;
  for (let p = el; p; p = p.parentElement) {
    const cs = getComputedStyle(p);
    if (cs.display === "none" || cs.visibility === "hidden") return true;
  }
  return false;
};
for (const el of document.querySelectorAll("body *")) {
  const leaf = el.matches("input, select, textarea, button, img, .btn, label")
    || [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
  if (!leaf) continue;
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) continue;
  if (r.right <= W + 2 && r.left >= -2) continue;
  if (scrolls(el) || unseen(el)) continue;
  found.push(`cut off: ${name(el)} at ${Math.round(r.left)}..${Math.round(r.right)} px of ${W}`);
  if (found.length >= 4) break;
}
return found;
""".replace("PARKED_SELECTORS", repr(", ".join(PARKED)))

# An em dash among the words on screen (CLAUDE.md: none anywhere).
EM_DASH = r"""
const text = document.body ? document.body.innerText : "";
const at = text.indexOf("\u2014");
return at < 0 ? null : text.slice(Math.max(0, at - 40), at + 40).replace(/\s+/g, " ");
"""

# Each pass of the walk: the screen, and whether the page is in dark mode.
LAPTOP = {"viewport": {"width": 1400, "height": 900}}
PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "fits": True}
TABLET = {"viewport": {"width": 768, "height": 1024}, "has_touch": True, "dark": True, "fits": True}

# A CSS rule among the page's visible words: "selector { property: value }".
# innerText leaves out <style> and hidden elements, so a match means the
# browser is showing a stylesheet as text. JSON does not match: its keys
# are quoted.
CSS_AS_TEXT = r"""
const text = document.body ? document.body.innerText : "";
const found = text.match(/[.#@:\w\]\)*][^{}\n]{0,80}\{\s*[a-z-]+\s*:\s*[^{}"]{1,200}\}/);
return found ? found[0].slice(0, 120) : null;
"""

# The one header (templates/core/base.html): what every page that has the
# app's header must show once, and only once. A page that is not an app
# page (login, a receipt) has no header and is not asked.
HEADER = r"""
const header = document.querySelector("header.pos-header");
if (!header) return [];
const wrong = [];
const once = (what, selector) => {
  const n = header.querySelectorAll(selector).length;
  if (n !== 1) wrong.push(`${n} ${what} in the header, expected 1`);
};
once("brand links", 'a[href="/dashboard/"].brand-title');
once("theme switches", "#themeIcon");
once("logout links", 'a[href="/logout/"]');
const icons = document.querySelectorAll("#themeIcon").length;
if (icons !== 1) wrong.push(`${icons} #themeIcon on the page, expected 1`);
// The shared parts never by onclick. A page's own header buttons still
// using onclick are counted by the ratchet (test_frontend_hygiene) instead.
const inline = header.querySelectorAll('[data-header][onclick], [onclick*="themeManager"]').length;
if (inline) wrong.push(`${inline} shared header part(s) by onclick`);
return wrong;
"""


# Each block directly inside <main> that is centred (equal side margins) or
# has a max-width in px must be as wide as it may be: the room <main> gives
# it, or its max-width, whichever is less.
#
# A box laid over the page from inside <main> (fixed or absolute: a toast, a
# floating button) is not a page block, and the width that page blocks get
# must not reach it: it stays on the screen. Until 27 Nov 2026 the till's
# "Order Sent" toast ran from -20 px across the whole screen on every
# device, stretched by the rule written for the page blocks
# (static/css/rasova.css), and this walk skipped it.
WIDTH = r"""
const main = document.querySelector("main");
if (!main) return [];
const room = main.clientWidth;
const W = document.documentElement.clientWidth;
const parked = PARKED_SELECTORS;
const wrong = [];
for (const el of main.children) {
  const cs = getComputedStyle(el);
  if (cs.display === "none") continue;
  const name = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "")
    + (el.className ? "." + String(el.className).trim().split(/\s+/).join(".") : "");
  if (cs.position === "fixed" || cs.position === "absolute") {
    // Closed until opened (the phone till's order page is hidden and slid
    // off to the right; a closed offcanvas likewise): as the cut-off check.
    // A toast waiting at opacity 0 is not closed: it shows where it stands.
    const closed = cs.visibility === "hidden"
      || el.matches(".modal:not(.show), [hidden], .offcanvas:not(.show), dialog:not([open])");
    const r = el.getBoundingClientRect();
    if (!closed && r.width >= 2 && r.height >= 2 && (r.left < -2 || r.right > W + 2) && !el.matches(parked)) {
      wrong.push(`${name} (${cs.position}) runs off the screen: ${Math.round(r.left)}..${Math.round(r.right)} px of ${W}`);
    }
    continue;
  }
  const max = cs.maxWidth.endsWith("px") ? parseFloat(cs.maxWidth) : Infinity;
  const centred = cs.marginLeft === cs.marginRight && parseFloat(cs.marginLeft) > 0;
  if (max === Infinity && !centred) continue;
  const want = Math.min(max, room);
  const width = el.getBoundingClientRect().width;
  if (width < want - 2) {
    wrong.push(`${name} is ${Math.round(width)} px wide with room for ${Math.round(want)} px`);
  }
}
return wrong;
""".replace("PARKED_SELECTORS", repr(", ".join(PARKED)))


def rows_a_phone_must_fit(r):
    """Rows that make pages draw their widest tables, so the phone pass sees
    them: a settled bill (Order History), an ingredient it used (Consumption)
    and a cover sold at the door (Covers). Without them those pages showed
    an empty state, and their tables, cut off on a real pub's phone, were
    never measured."""
    from covers.models import Cover
    from django.utils import timezone
    from inventory.models import Recipe
    from orders.models import Order, OrderItem, Payment

    settled = Order.objects.create(
        tenant=r.tenant, outlet=r.outlet, table=r.table2, created_by=r.owner, status="closed",
        source="dine_in", customer_name=f"{r.tag} Regular Guest",
    )
    OrderItem.objects.create(
        order=settled, menu_item=r.menu_item, quantity=3, price=r.menu_item.price,
        gst_percentage=r.menu_item.gst_percentage, total_price=r.menu_item.price * 3,
    )
    settled.recalculate_totals()
    Payment.objects.create(order=settled, method="cash", amount=settled.grand_total, created_by=r.owner)
    Recipe.objects.create(menu_item=r.menu_item, inventory_item=r.inventory_item,
                          quantity_required=Decimal("0.25"), unit="kg")
    Cover.objects.create(
        tenant=r.tenant, outlet=r.outlet, code="ALPHA1", amount=Decimal("1000"), guests=2,
        guest_name=f"{r.tag} Door Guest", paid_by="cash", sold_by=r.owner,
        business_date=get_business_date(timezone.now(), r.outlet),
    )


def owner_pages(restaurant):
    """(name, url) for every route that answers the owner's GET with a 200
    HTML page, ids pointing at the restaurant's own rows."""
    client = Client(raise_request_exception=False)
    client.force_login(restaurant.owner)
    pages = []
    for namespace, name, route in _routes(get_resolver().url_patterns):
        if not name or name in NOT_WALKED:
            continue
        if namespace.startswith("admin") or route.startswith("admin/") or route.startswith("^media/"):
            continue
        spec = re.findall(r"<(?:\w+:)?(\w+)>", route)
        if any(k not in REGISTRY for k in spec):
            continue    # keyed by a guest's token; the tenant walk lists these as public
        try:
            url = reverse(f"{namespace}:{name}" if namespace else name,
                          kwargs={k: REGISTRY[k](restaurant, route) for k in spec})
        except Exception:  # noqa: BLE001 -- the tenant walk reports a route that cannot be reversed
            continue
        response = client.get(url, HTTP_ACCEPT="text/html")
        if (response.status_code == 200 and not response.streaming
                and response.get("Content-Type", "").startswith("text/html")):
            pages.append((name, url))
    return pages


class BrowserSmokeWalkTest(BrowserTestCase):

    def setUp(self):
        freeze_ratelimit_clock(self)
        self.restaurant = Restaurant("Alpha", "walk-alpha")
        rows_a_phone_must_fit(self.restaurant)

    def open(self, context, url, fits=False):
        """Open one page; return what went wrong on it, as a list of lines.
        `fits`: also look for anything cut off at the screen's edges."""
        problems = []
        page = context.new_page()
        page.set_default_timeout(30_000)
        page.on("pageerror", lambda error: problems.append(f"uncaught: {error}"))
        page.on("console", lambda message: problems.append(_console_error(message))
                if message.type == "error" else None)
        page.on("response", lambda response: problems.append(
            f"static file {response.status}: {urlparse(response.url).path}")
            if response.status >= 400 and "/static/" in response.url else None)
        page.on("requestfailed", lambda request: problems.append(
            f"static file failed: {urlparse(request.url).path}")
            if "/static/" in request.url else None)
        try:
            page.goto(self.live_server_url + url, wait_until="load")
            # The first requests a page makes by itself (its poll, its list)
            # answer within this; a page that never goes quiet is not waited for.
            try:
                page.wait_for_load_state("networkidle", timeout=2_000)
            except Exception:  # noqa: BLE001 -- a polling or streaming page
                pass
            css = page.evaluate("async () => {" + CSS_AS_TEXT + "}")
            if css:
                problems.append(f"stylesheet shown as text: {css!r}")
            problems.extend(page.evaluate("async () => {" + HEADER + "}"))
            problems.extend(page.evaluate("async () => {" + WIDTH + "}"))
            dash = page.evaluate("async () => {" + EM_DASH + "}")
            if dash:
                problems.append(f"em dash on screen: {dash!r}")
            if fits:
                problems.extend(page.evaluate("async () => {" + CUT_OFF + "}"))
        except Exception as e:  # noqa: BLE001 -- reported as this page's problem
            problems.append(f"could not open: {str(e).splitlines()[0]}")
        finally:
            page.close()
        return problems

    def walk(self, screen):
        """Every page the owner can open, on one screen; the findings."""
        pages = owner_pages(self.restaurant)
        client = Client()
        client.force_login(self.restaurant.owner)
        context = self.browser.new_context(viewport=screen["viewport"], is_mobile=screen.get("is_mobile", False),
                                           has_touch=screen.get("has_touch", False))
        if screen.get("dark"):
            # What the theme switch saves; static/js/core/theme_boot.js reads it before the first paint.
            context.add_init_script('try { localStorage.setItem("dark", "true"); } catch (e) {}')
        findings = []
        try:
            context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                  "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                                  "url": self.live_server_url}])
            for name, url in pages:
                # The same problem twice on one page is one finding.
                for problem in dict.fromkeys(self.open(context, url, fits=screen.get("fits", False))):
                    findings.append(f"{url} ({name}): {problem}")
        finally:
            context.close()
            self.wait_for_the_server()
        # 67 on 13 Nov 2026. Far fewer means the URL map or the fixture moved.
        self.assertGreater(len(pages), 60, f"the walk opened too few pages: {[name for name, _ in pages]}")
        return findings

    def assertWalkClean(self, findings, screen):
        self.assertEqual(findings, [], f"\n\nThe browser smoke walk ({screen}) found problems:\n  " + "\n  ".join(findings))

    def test_every_page_the_owner_can_open_loads_clean(self):
        self.assertWalkClean(self.walk(LAPTOP), "laptop, 1400 px")

    def test_every_page_fits_a_phone(self):
        self.assertWalkClean(self.walk(PHONE), "phone, 390 px")

    def test_every_page_fits_a_tablet_in_dark_mode(self):
        self.assertWalkClean(self.walk(TABLET), "tablet, 768 px, dark")
