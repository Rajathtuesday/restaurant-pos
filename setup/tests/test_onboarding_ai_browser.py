"""
Setup step 2's AI Import, in a phone's browser, from the photo to the dishes.

Why: on 28 Nov 2026 a new pub's owner picked a menu photo at step 2 and read
"Import failed. Try again." The menu importer queues the job (Celery) and
answers {queued, task_id}; the Menu page then asks for the result until it is
done, but step 2's own script still waited for {success, message}, so it
said "failed" at once and never asked. It also sent the photo as "image",
while the importer reads "file", so the job never saw the photo. Step 2 now
uses the Menu page's importer (static/js/menu/manage_parts.js).

The job is queued here as in production, where Redis and a worker take it:
it runs in this process and leaves its result in the cache for the asking.
Claude is not called (AIService.parse_menu answers).

Run: python manage.py test setup.tests.test_onboarding_ai_browser
"""
from unittest.mock import patch

from core.tests.browser import BrowserTestCase
from menu.models import MenuItem
from tenants.models import TenantFeatureOverride
from tenants.services.restaurant_setup import create_restaurant

PHONE = {"width": 390, "height": 844, "mobile": True}
# A 1 x 1 PNG: the photo the owner picks
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
MENU = [
    {"category": "Draught Beer", "items": [{"name": "Lager Pint", "price": 320, "is_veg": True}]},
    {"category": "Bar Bites", "items": [{"name": "Masala Peanuts", "price": 160, "is_veg": True},
                                        {"name": "Chicken Wings", "price": 420, "is_veg": False}]},
]

# The Menu page itself, not just any /menu/ address (/menu/file/check/ is one)
MENU_PAGE = {"selector": "#menuManagementSearch"}  # in the Menu page body (the header folds on a phone)

PICK_A_PHOTO = """
const input = document.getElementById("ai-file-input");
const bytes = Uint8Array.from(atob("PNG"), (c) => c.charCodeAt(0));
const files = new DataTransfer();
files.items.add(new File([bytes], "menu.png", { type: "image/png" }));
input.files = files.files;
input.dispatchEvent(new Event("change", { bubbles: true }));
return await t.until(() => {
  const ok = document.getElementById("ai-success");
  const bad = document.getElementById("ai-failed");
  if (ok && !ok.hidden) return "done: " + t.text("#ai-success-msg");
  if (bad && !bad.hidden) return "failed: " + bad.textContent.trim();
  return null;
});
""".replace("PNG", PNG)


def queued(task, **kwargs):
    """The worker, in this process: as Redis and celery do on the server."""
    return task.apply(kwargs=kwargs)


class StepTwoAiImportTest(BrowserTestCase):

    def setUp(self):
        self.tenant, self.outlet, self.owner = create_restaurant(
            name="Photo Pub", tenant_type="pub", owner_username="photo_owner", owner_password="pw-123456")
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="ai_menu_import", enabled=True)

    def pick(self, **reader):
        with patch("menu.views.ai_views.dispatch", side_effect=queued), \
                patch("core.ai_service.AIService.parse_menu", **reader) as parse:
            seen = self.run_page("/setup/onboard/?step=2", [("pick", PICK_A_PHOTO)], user=self.owner, viewport=PHONE)
        return seen, parse

    def test_a_menu_photo_becomes_dishes_and_the_owner_is_told(self):
        seen, parse = self.pick(return_value=MENU)
        self.assertEqual(seen["errors"], [])
        self.assertEqual(seen["results"]["pick"], "done: AI imported 3 items.")
        # the photo itself reached the reader
        self.assertEqual(parse.call_args.kwargs["mime_type"], "image/png")
        self.assertTrue(parse.call_args.kwargs["image_bytes"].startswith(b"\x89PNG"))
        names = set(MenuItem.objects.for_outlet(self.tenant, self.outlet).values_list("name", flat=True))
        self.assertEqual(names, {"Lager Pint", "Masala Peanuts", "Chicken Wings"})

    def menu_file_from_another_pub(self):
        """A Rasova menu file, as Menu file's Save gives it: Beta's menu
        (made once per test), and how many dishes it holds."""
        import json

        from core.tests.test_tenant_walk import Restaurant
        from menu.services.menu_file import export_menu
        if not hasattr(self, "_menu_file"):
            beta = Restaurant("Beta", "photo-beta")
            self._menu_file = (json.dumps(export_menu(beta.outlet)),
                               MenuItem.objects.for_outlet(beta.tenant, beta.outlet).count())
        return self._menu_file

    def pick_menu_file(self, page, input_id, then=""):
        """Pick the menu file into `input_id` (as the phone's picker would)."""
        text, _ = self.menu_file_from_another_pub()
        return f"""
            const files = new DataTransfer();
            files.items.add(new File([{text!r}], "menu.json", {{ type: "application/json" }}));
            const input = document.getElementById("{input_id}");
            input.files = files.files;
            input.dispatchEvent(new Event("change", {{ bubbles: true }}));
            {then}"""

    def test_a_menu_file_at_step_two_goes_straight_to_its_check(self):
        """28 Nov 2026: the owner gave the menu file to step 2's AI card four
        times; each time it said where to go, and he never got there. Now
        it goes there itself: Menu file's check screen, the file already read."""
        _, dishes = self.menu_file_from_another_pub()
        with patch("menu.views.ai_views.dispatch", side_effect=queued) as dispatch:
            seen = self.run_page("/setup/onboard/?step=2", [
                ("pick", self.pick_menu_file(None, "ai-file-input"), "/menu/file/check/"),
                ("check", 'return document.body.innerText.includes("Ready to load");'),
                ("load", 't.click("#loadMenuFile");', MENU_PAGE),
            ], user=self.owner, viewport=PHONE)
        self.assertEqual(seen["errors"], [])
        self.assertTrue(seen["results"]["check"])
        dispatch.assert_not_called()
        self.assertEqual(MenuItem.objects.for_outlet(self.tenant, self.outlet).count(), dishes)

    def test_a_menu_file_in_the_menu_pages_ai_import_goes_there_too(self):
        _, dishes = self.menu_file_from_another_pub()
        open_and_pick = self.pick_menu_file(None, "aiMenuFile", then='t.click("#aiProcessBtn");')
        seen = self.run_page("/menu/", [
            ("pick", open_and_pick, "/menu/file/check/"),
            ("load", 't.click("#loadMenuFile");', MENU_PAGE),
        ], user=self.owner, viewport=PHONE)
        self.assertEqual(seen["errors"], [])
        self.assertEqual(MenuItem.objects.for_outlet(self.tenant, self.outlet).count(), dishes)

    def test_a_job_that_fails_says_why(self):
        seen, _ = self.pick(side_effect=RuntimeError("the reader broke"))
        self.assertTrue(seen["results"]["pick"].startswith("failed: AI import failed: "), seen["results"])
        self.assertFalse(MenuItem.objects.for_outlet(self.tenant, self.outlet).exists())
