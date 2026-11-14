"""
The colour walk (V3): every page the owner can open, in light and in dark,
in a real browser.

Why: on 12 November 2026 every page in light mode was white cards on a black
page, and no test saw it, because no test looked at a colour. The theme test
(core/tests/test_theme_browser.py) checks one page at the root. This walks
them all, in both modes, and fails on:

  - an app page (one with base.html's header) whose page colour is not the
    mode's: dark behind a light page, or light behind a dark one;
  - text that cannot be seen: its colour against the solid colour behind it
    below a contrast of MIN_CONTRAST (white on white, black on black).
    Text over an image or a gradient is not judged: its background is not
    one colour.

The pages are the smoke walk's (core/tests/test_browser_smoke_walk.py): a new
page is walked the day its URL is added.

Run: python manage.py test core.tests.test_colour_walk
"""
from django.conf import settings
from django.test import Client

from core.testing import freeze_ratelimit_clock
from core.tests.browser import BrowserTestCase, _wait_until_the_server_is_idle
from core.tests.test_browser_smoke_walk import owner_pages
from core.tests.test_tenant_walk import Restaurant

# 1 is the same colour; WCAG asks 4.5 for body text. Below this, text is as
# good as invisible: what a page that forgot one of the modes looks like.
MIN_CONTRAST = 1.5

LOOK = r"""
async (minContrast) => {
  // The theme fades colours for 0.3 s: wait until nothing moves.
  for (let i = 0; i < 40 && document.getAnimations().some((a) => a.playState === "running"); i++) {
    await new Promise((r) => setTimeout(r, 50));
  }
  const parse = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const [r, g, b, a = 1] = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
    return { r, g, b, a };
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const over = (top, under) => ({
    r: top.r * top.a + under.r * (1 - top.a),
    g: top.g * top.a + under.g * (1 - top.a),
    b: top.b * top.a + under.b * (1 - top.a), a: 1,
  });
  const contrast = (x, y) => {
    const [hi, lo] = [lum(x), lum(y)].sort((p, q) => q - p);
    return (hi + 0.05) / (lo + 0.05);
  };
  // The solid colour painted behind an element, or null when an image or a
  // gradient is behind it (not one colour, not judged).
  const behind = (el) => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const s = getComputedStyle(e);
      if (s.backgroundImage && s.backgroundImage !== "none") return null;
      const c = parse(s.backgroundColor);
      if (c && c.a > 0) {
        layers.push(c);
        if (c.a >= 1) break;
      }
    }
    let colour = { r: 255, g: 255, b: 255, a: 1 };   // the browser's canvas
    for (const c of layers.reverse()) colour = over(c, colour);
    return colour;
  };
  const dark = document.documentElement.classList.contains("dark");
  const found = [];
  if (document.querySelector("header.pos-header")) {
    const page = behind(document.body);
    const l = page ? lum(page) : null;
    if (l !== null && (dark ? l > 0.2 : l < 0.6)) {
      found.push(`page colour ${getComputedStyle(document.body).backgroundColor} in ${dark ? "dark" : "light"} mode`);
    }
  }
  const seen = new Set();
  for (const el of document.body.querySelectorAll("*")) {
    if (!el.childNodes.length) continue;
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(" ").trim();
    if (!own) continue;
    const s = getComputedStyle(el);
    if (s.visibility !== "visible" || Number(s.opacity) === 0 || el.closest("[hidden], .d-none, .is-hidden")) continue;
    const box = el.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) continue;
    if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "OPTION"].includes(el.tagName)) continue;
    let fade = 1;
    for (let e = el; e; e = e.parentElement) fade *= Number(getComputedStyle(e).opacity);
    if (fade < 0.05) continue;
    const bg = behind(el);
    const fg = parse(s.color);
    if (!bg || !fg || s.webkitTextFillColor === "transparent") continue;
    const ratio = contrast(over(fg, bg), bg);
    if (ratio < minContrast) {
      const where = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
        (el.classList.length ? "." + [...el.classList].slice(0, 2).join(".") : "");
      const line = `${where} "${own.slice(0, 30)}" ${s.color} on ${getComputedStyle(el).backgroundColor === "rgba(0, 0, 0, 0)" ? "rgb(" + [bg.r, bg.g, bg.b].map(Math.round).join(", ") + ")" : getComputedStyle(el).backgroundColor} (${ratio.toFixed(2)})`;
      if (!seen.has(where)) { seen.add(where); found.push(line); }
    }
  }
  return found;
}
"""


class ColourWalkTest(BrowserTestCase):

    def setUp(self):
        freeze_ratelimit_clock(self)
        self.restaurant = Restaurant("Alpha", "colour-alpha")

    def walk(self, pages, dark):
        client = Client()
        client.force_login(self.restaurant.owner)
        context = self.browser.new_context(viewport={"width": 1400, "height": 900})
        context.add_init_script(f"try {{ localStorage.setItem('dark', '{str(dark).lower()}'); }} catch (e) {{}}")
        context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                              "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                              "url": self.live_server_url}])
        findings = []
        try:
            for name, url in pages:
                page = context.new_page()
                page.set_default_timeout(30_000)
                try:
                    page.goto(self.live_server_url + url, wait_until="load")
                    try:
                        page.wait_for_load_state("networkidle", timeout=2_000)
                    except Exception:  # noqa: BLE001 -- a polling page
                        pass
                    for problem in page.evaluate(LOOK, MIN_CONTRAST):
                        findings.append(f"{'dark' if dark else 'light'} {url} ({name}): {problem}")
                except Exception as e:  # noqa: BLE001 -- the smoke walk reports pages that do not open
                    findings.append(f"{url} ({name}): could not look: {str(e).splitlines()[0]}")
                finally:
                    page.close()
        finally:
            context.close()
        return findings

    def test_every_page_in_both_modes_has_its_colours_and_no_invisible_text(self):
        pages = owner_pages(self.restaurant)
        try:
            findings = self.walk(pages, dark=False) + self.walk(pages, dark=True)
        finally:
            _wait_until_the_server_is_idle()
        self.assertEqual(findings, [], "\n\nThe colour walk found problems:\n  " + "\n  ".join(findings))
        self.assertGreater(len(pages), 60, "the walk opened too few pages")
