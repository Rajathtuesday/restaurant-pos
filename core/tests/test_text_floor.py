"""
Text a cashier can read at arm's length (V11).

Why: the luxury style leaned on very small, wide-spaced capitals: labels at
0.6rem (about 10px), some at 0.55rem. Elegant on a laptop, hard on a tablet a
metre away in a dim room. The floor on the screens used during service:

  - no text below FLOOR_PX (12px), on a laptop or a phone;
  - on a phone, every field a person types into at 16px or more: smaller
    makes an iPhone zoom the page when the field is tapped.

The screens are the ones staff work on through a service: billing, the token
counter and its bill, the floor plan, the kitchen display and the bill page.

Run: python manage.py test core.tests.test_text_floor
"""
from django.conf import settings
from django.test import Client
from django.urls import reverse

from core.testing import freeze_ratelimit_clock
from core.tests.browser import BrowserTestCase
from core.tests.test_tenant_walk import Restaurant

FLOOR_PX = 12
PHONE_INPUT_PX = 16

MEASURE = r"""
async ([floor, inputFloor, phone]) => {
  for (let i = 0; i < 40 && document.getAnimations().some((a) => a.playState === "running"); i++) {
    await new Promise((r) => setTimeout(r, 50));
  }
  const found = new Map();
  const shown = (el) => {
    const s = getComputedStyle(el);
    if (s.visibility !== "visible" || s.display === "none") return false;
    const box = el.getBoundingClientRect();
    return box.width >= 1 && box.height >= 1;
  };
  const name = (el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
    (el.classList.length ? "." + [...el.classList].slice(0, 2).join(".") : "");
  for (const el of document.body.querySelectorAll("*")) {
    if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "OPTION"].includes(el.tagName)) continue;
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(" ").trim();
    if (!own || !shown(el)) continue;
    const size = parseFloat(getComputedStyle(el).fontSize);
    if (size < floor - 0.01) {
      const key = name(el);
      if (!found.has(key)) found.set(key, `${key} "${own.slice(0, 24)}" at ${size.toFixed(1)}px`);
    }
  }
  if (phone) {
    for (const el of document.querySelectorAll("input, textarea, select")) {
      if (["hidden", "checkbox", "radio", "range", "color", "file", "button", "submit"].includes(el.type) || !shown(el)) continue;
      const size = parseFloat(getComputedStyle(el).fontSize);
      if (size < inputFloor - 0.01) {
        const key = "field " + name(el);
        if (!found.has(key)) found.set(key, `${key} at ${size.toFixed(1)}px (an iPhone zooms the page)`);
      }
    }
  }
  return [...found.values()];
}
"""


class TextFloorTest(BrowserTestCase):

    def setUp(self):
        freeze_ratelimit_clock(self)
        self.restaurant = Restaurant("Alpha", "floor-alpha")

    def screens(self):
        order = self.restaurant.order
        return [
            ("billing", reverse("billing-view")),
            ("floor plan", reverse("table-dashboard")),
            ("kitchen", reverse("kitchen-view")),
            ("bill", reverse("bill-view", args=[order.id])),
            ("token counter", reverse("token-dashboard")),
        ]

    def measure(self, viewport, phone):
        client = Client()
        client.force_login(self.restaurant.owner)
        context = self.browser.new_context(viewport=viewport, is_mobile=phone, has_touch=phone)
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                              "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                              "url": self.live_server_url}])
        findings = []
        try:
            for what, url in self.screens():
                page = context.new_page()
                try:
                    page.goto(self.live_server_url + url, wait_until="load")
                    try:
                        page.wait_for_load_state("networkidle", timeout=2_000)
                    except Exception:  # noqa: BLE001 -- a polling page
                        pass
                    for problem in page.evaluate(MEASURE, [FLOOR_PX, PHONE_INPUT_PX, phone]):
                        findings.append(f"{'phone' if phone else 'laptop'} {what} {url}: {problem}")
                finally:
                    page.close()
        finally:
            context.close()
        return findings

    def test_service_screens_have_no_text_below_the_floor(self):
        try:
            findings = (self.measure({"width": 1400, "height": 900}, phone=False)
                        + self.measure({"width": 390, "height": 844}, phone=True))
        finally:
            self.wait_for_the_server()
        self.assertEqual(findings, [], "\n\nText below the floor:\n  " + "\n  ".join(findings))
