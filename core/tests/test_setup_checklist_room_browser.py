"""
The setup checklist badge never leaves a page's last button under it.

"Setup 3/5 complete" is fixed in the bottom right corner of the owner's
setup pages (/dashboard/, /setup/, /menu/) until setup is done. A badge
over the page is fine while the page scrolls under it; a button that
stays under it with the page scrolled to its end can't be reached. A new
owner setting up on a phone is exactly who sees it. Seen recording the
phone reels, 27 Nov 2026: Menu file's "Load into Indiranagar" half under
the badge.

Run: python manage.py test core.tests.test_setup_checklist_room_browser
"""
import json

from core.context_processors import shows_setup_checklist
from core.tests.browser import BrowserTestCase
from core.tests.test_browser_smoke_walk import owner_pages
from core.tests.test_tenant_walk import Restaurant

PHONE = {"width": 390, "height": 844, "mobile": True}

# Every button, link and field the badge covers with the page scrolled as
# far down as it goes. null when the page has no badge.
COVERED = """
if (!document.getElementById("setup-checklist-widget")) return null;
const badge = await t.until(() => {
  const w = document.getElementById("setup-checklist-widget");
  return !w.hidden && w.getBoundingClientRect().height > 0 ? w : null;
});
// instant: the page scrolls smoothly, and a measure taken mid-scroll sees nothing
window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "instant" });
await t.until(() => Math.abs(innerHeight + scrollY - document.documentElement.scrollHeight) < 2);
await new Promise((go) => requestAnimationFrame(() => requestAnimationFrame(go)));
const b = badge.getBoundingClientRect();
const covered = [];
for (const el of document.querySelectorAll("main button, main a, main input, main select, main textarea")) {
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) continue;
  const cs = getComputedStyle(el);
  if (cs.visibility === "hidden" || cs.display === "none" || el.closest("[hidden], .modal:not(.show)")) continue;
  if (r.right > b.left && r.left < b.right && r.bottom > b.top && r.top < b.bottom) {
    covered.push((el.textContent || el.name || el.tagName).trim().replace(/\\s+/g, " ").slice(0, 40));
  }
}
return covered;
"""


class SetupBadgeLeavesRoomTest(BrowserTestCase):

    def setUp(self):
        from menu.services.menu_file import export_menu
        from setup.models import PaymentConfig

        self.r = Restaurant("Alpha", "badge-alpha")
        # setup not finished: no payment methods yet, so the badge shows
        PaymentConfig.objects.filter(tenant=self.r.tenant, outlet=self.r.outlet).delete()
        self.menu_file = json.dumps(export_menu(self.r.outlet))

    def covered(self, url, before=()):
        seen = self.run_page(url, [*before, ("covered", COVERED)], user=self.r.owner, viewport=PHONE)
        if seen["errors"]:
            return [f"{url}: {e}" for e in seen["errors"]]
        return [f"{url}: {what!r} stays under the badge" for what in seen["results"]["covered"] or []]

    def test_no_setup_page_ends_under_the_badge_on_a_phone(self):
        pages = [url for _, url in owner_pages(self.r) if shows_setup_checklist(url)]
        self.assertGreater(len(pages), 10, pages)
        found = []
        for url in pages:
            found += self.covered(url)
        # Menu file's check page, the one seen on the phone: it needs a file.
        upload = f"""
            const input = document.querySelector("input[name=menu_file]");
            const files = new DataTransfer();
            files.items.add(new File([{json.dumps(self.menu_file)}], "menu.json", {{ type: "application/json" }}));
            input.files = files.files;
            t.click("button[type=submit]");"""
        found += self.covered("/menu/file/", before=[("upload", upload, "/menu/file/check/")])
        self.assertEqual(found, [], "\n  " + "\n  ".join(found))
