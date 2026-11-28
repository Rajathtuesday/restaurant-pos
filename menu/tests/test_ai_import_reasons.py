"""
The AI menu import tells the owner why it couldn't read a file.

Why: on 28 Nov 2026 the owner gave the photo reader a Rasova menu file
(JSON) at setup step 2. The reader knew what was wrong ("Couldn't read that
file. Please upload a photo (JPG/PNG), a PDF, ..."), but the job replaced
every error with "Something went wrong while importing this file", so that
is all the phone said. The reader's refusals are now UserErrors
(core.ai_service.AIReadError) and the job shows their words; a real crash
still gets the plain line. And a Rasova menu file belongs on Menu, Menu file,
which checks it first: the importer now says so before queueing anything.

Run: python manage.py test menu.tests.test_ai_import_reasons
"""
import base64
import json
import os
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from core.tests.fake_claude import FakeClaude
from core.tests.test_tenant_walk import Restaurant
from tenants.models import TenantFeatureOverride

MENU_FILE = json.dumps({"format": "rasova-menu", "version": 1, "categories": []}).encode()
NOT_A_PHOTO = b'{"just": "some json, not a photo"}'


def claude_is_set_up():
    """As on production: a key, and a client (a fake, never the real API)."""
    return (patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch("core.ai_service.anthropic.Anthropic", return_value=FakeClaude({"categories": []})))


class ReasonsTest(TestCase):

    def setUp(self):
        self.r = Restaurant("Alpha", "aireason-alpha")
        TenantFeatureOverride.objects.update_or_create(tenant=self.r.tenant, feature="ai_menu_import",
                                                       defaults={"enabled": True})
        self.client = Client()
        self.client.force_login(self.r.owner)

    def run_job(self, **kwargs):
        from menu.tasks import ai_import_menu
        result = ai_import_menu.apply(kwargs={"tenant_id": self.r.tenant.id, "outlet_id": self.r.outlet.id,
                                              "text": None, "image_b64": None, "mime_type": None, **kwargs})
        return cache.get(f"ai_import:{result.id}")

    def test_a_rasova_menu_file_is_sent_to_menu_file_not_queued(self):
        upload = SimpleUploadedFile("menu.json", MENU_FILE, content_type="application/json")
        with patch("menu.views.ai_views.dispatch") as dispatch:
            response = self.client.post("/menu/ai-import/", {"file": upload})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Menu file", response.json()["error"])
        dispatch.assert_not_called()

    def test_the_readers_own_reason_reaches_the_owner(self):
        key, client = claude_is_set_up()
        with key, client:
            answer = self.run_job(image_b64=base64.b64encode(NOT_A_PHOTO).decode(), mime_type="application/json")
        self.assertEqual(answer["status"], "error")
        self.assertTrue(answer["error"].startswith("Couldn't read that file."), answer)

    def test_a_crash_still_gets_the_plain_line(self):
        with patch("core.ai_service.AIService.parse_menu", side_effect=RuntimeError("an internal detail")):
            answer = self.run_job(text="x")
        self.assertEqual(answer["status"], "error")
        self.assertNotIn("an internal detail", answer["error"])
        self.assertIn("Something went wrong", answer["error"])

    def test_with_the_queue_down_the_reason_is_shown_too(self):
        upload = SimpleUploadedFile("menu.bin", NOT_A_PHOTO, content_type="application/octet-stream")
        key, client = claude_is_set_up()
        with key, client, patch("menu.views.ai_views.dispatch", side_effect=Exception("queue down")):
            response = self.client.post("/menu/ai-import/", {"file": upload})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["error"].startswith("Couldn't read that file."), response.json())
