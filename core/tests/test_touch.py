"""
Touch, not hover (V15).

Why: cards lifted on hover (transform: translateY(-2px)). A touch screen keeps
the last tapped element "hovered", so on a tablet a card stayed lifted after
it was tapped. Every :hover rule that moves something must sit inside
@media (hover: hover), where only a mouse gets it; a finger gets the pressed
state in static/css/rasova.css. Fifteen rules broke this on 14 Nov 2026.

On a phone, the controls of the screens used during service are at least
TAP_PX tall and wide (the size a fingertip hits reliably).

Run: python manage.py test core.tests.test_touch
"""
import subprocess

from django.conf import settings
from django.test import SimpleTestCase

from core.css_hover import loose_hover_lifts, style_sources, wrap

SKIP = ("public/", "md_files/", "static/vendor/", "rasova_android/", "docs/")


def styled_files():
    out = subprocess.run(["git", "ls-files", "*.css", "*.html"], cwd=settings.BASE_DIR,
                         capture_output=True, text=True, check=True).stdout.split()
    for path in out:
        if not path.startswith(SKIP):
            yield path


class HoverLiftTest(SimpleTestCase):

    def test_no_hover_lift_outside_hover_hover(self):
        found = []
        for path in styled_files():
            text = (settings.BASE_DIR / path).read_text(encoding="utf-8")
            for css in style_sources(text, path.endswith(".html")):
                found += [f"{path}: {' '.join(sel.split())}" for sel in loose_hover_lifts(css)]
        self.assertEqual(found, [], "a :hover rule that moves something belongs in @media (hover: hover)")

    def test_the_finder_and_the_wrap(self):
        css = (".a:hover { transform: translateY(-2px); }\n"
               ".b:hover { color: red; }\n"
               "@media (hover: hover) { .c:hover { transform: scale(1.1); } }\n"
               "/* .d:hover { transform: none; } */\n"
               "@media (max-width: 600px) { .e:hover { transform: none; } }\n")
        self.assertEqual(loose_hover_lifts(css), [".a:hover", ".e:hover"])
        fixed = wrap(css)
        self.assertEqual(loose_hover_lifts(fixed), [])
        self.assertEqual(fixed.count("\n"), css.count("\n"), "wrapped on the same lines")
        self.assertIn("@media (hover: hover) { .a:hover { transform: translateY(-2px); } }", fixed)

    def test_touch_screens_get_a_pressed_state(self):
        css = (settings.BASE_DIR / "static" / "css" / "rasova.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"@media \(hover: none\) \{\s*:is\(button, a, \[data-action\], \[role=\"button\"\]\):active")


TAP_PX = 44

TAPS = r"""
async (minPx) => {
  const found = new Map();
  for (const el of document.querySelectorAll('button, a[href], [data-action], [role="button"], input:not([type=hidden]), select')) {
    const s = getComputedStyle(el);
    if (s.visibility !== "visible" || s.display === "none" || s.pointerEvents === "none") continue;
    if (el.closest("[hidden], .d-none, .is-hidden, .modal:not(.show), .offcanvas:not(.show)")) continue;
    const box = el.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) continue;
    // A link inside running text is read, not aimed at.
    if (el.tagName === "A" && s.display === "inline" && el.parentElement && /P|LI|SPAN|SMALL|TD/.test(el.parentElement.tagName)) continue;
    if (box.height < minPx - 0.5 || box.width < minPx - 0.5) {
      const key = el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") +
        (el.classList.length ? "." + [...el.classList].slice(0, 2).join(".") : "") +
        (el.dataset.action ? `[${el.dataset.action}]` : "");
      const label = (el.innerText || el.value || el.title || el.getAttribute("aria-label") || "").trim().slice(0, 20);
      if (!found.has(key)) found.set(key, `${key} "${label}" ${Math.round(box.width)}x${Math.round(box.height)}`);
    }
  }
  return [...found.values()];
}
"""


from core.tests import test_text_floor as _floor  # noqa: E402  (the same five screens)


class TapTargetTest(_floor.TextFloorTest):
    """On a phone, the service screens' controls are a fingertip wide."""

    def test_service_screens_have_no_text_below_the_floor(self):
        pass    # TextFloorTest's own test; this class only borrows its screens

    def test_controls_on_a_phone_are_big_enough_to_tap(self):
        from django.conf import settings as s
        from django.test import Client
        client = Client()
        client.force_login(self.restaurant.owner)
        context = self.browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        context.add_cookies([{"name": s.SESSION_COOKIE_NAME, "value": client.cookies[s.SESSION_COOKIE_NAME].value,
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
                    findings += [f"{what} {url}: {p}" for p in page.evaluate(TAPS, TAP_PX)]
                finally:
                    page.close()
        finally:
            context.close()
            self.wait_for_the_server()
        self.assertEqual(findings, [], "\n\nControls too small to tap:\n  " + "\n  ".join(findings))
