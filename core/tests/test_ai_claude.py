"""
The AI on Claude (core/ai_service.py, 15 Nov 2026; it was Gemini before).

What is sent (model, the file as an image or document block, the JSON
schema), what comes back (the same lists and dicts the callers always
had), and every way it can fail: an API error, a refusal, an answer cut
off. No test reaches the real API: each uses FakeClaude, and the service
never builds a real client without ANTHROPIC_API_KEY.
Run: python manage.py test core.tests.test_ai_claude
"""
import base64
import io
import os
from unittest.mock import patch

from django.test import SimpleTestCase
from PIL import Image

from core import ai_service
from core.ai_service import AIService
from core.tests.fake_claude import FakeClaude

MENU = {"categories": [{"category": "Starters", "items": [{"name": "Paneer Tikka", "price": 260, "is_veg": True}]}]}


def jpeg():
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="JPEG")
    return out.getvalue()


class ClientTest(SimpleTestCase):

    def test_no_key_no_client_even_with_a_login_profile_on_disk(self):
        # The SDK would read an `ant auth login` profile when no key is
        # given; the service must not build a client at all.
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}), \
             patch("core.ai_service.anthropic.Anthropic") as made:
            self.assertIsNone(AIService().client)
        made.assert_not_called()

    def test_a_key_builds_the_client_with_that_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}), \
             patch("core.ai_service.anthropic.Anthropic") as made:
            AIService()
        made.assert_called_once_with(api_key="sk-test")


class MenuTest(SimpleTestCase):

    def test_a_pdf_goes_as_a_document_to_sonnet_with_the_menu_schema(self):
        fake = FakeClaude(MENU)
        result = AIService(client=fake).parse_menu(image_bytes=b"%PDF-1.4 menu", mime_type="application/pdf")
        self.assertEqual(result, MENU["categories"])
        call = fake.calls[0]
        self.assertEqual(call["model"], "claude-sonnet-5-5")
        self.assertEqual(call["endpoint"], "beta")                  # with the refusal fallback
        self.assertEqual(call["fallbacks"], "default")
        self.assertEqual(call["output_config"]["format"]["schema"], ai_service.MENU_SCHEMA)
        first = call["messages"][0]["content"][0]
        self.assertEqual(first["type"], "document")
        self.assertEqual(base64.b64decode(first["source"]["data"]), b"%PDF-1.4 menu")

    def test_a_photo_goes_as_a_jpeg_image(self):
        fake = FakeClaude(MENU)
        AIService(client=fake).parse_menu(image_bytes=jpeg(), mime_type="image/png")
        block = fake.calls[0]["messages"][0]["content"][0]
        self.assertEqual((block["type"], block["source"]["media_type"]), ("image", "image/jpeg"))

    def test_pasted_text_goes_as_text_before_the_instructions(self):
        fake = FakeClaude(MENU)
        AIService(client=fake).parse_menu(text="Paneer Tikka 260")
        content = fake.calls[0]["messages"][0]["content"]
        self.assertIn("Paneer Tikka 260", content[0]["text"])
        self.assertIn("categories", content[-1]["text"])

    def test_a_refusal_or_a_cut_off_answer_falls_back_to_the_plain_parser(self):
        for stop in ("refusal", "max_tokens"):
            fake = FakeClaude(text='{"categories": [', stop_reason=stop)
            result = AIService(client=fake).parse_menu(text="STARTERS\nDal Tadka 240")
            self.assertEqual(result[0]["items"][0]["name"], "Dal Tadka", stop)

    def test_a_refused_photo_says_so_instead_of_pretending(self):
        fake = FakeClaude(stop_reason="refusal")
        with self.assertRaises(Exception):
            AIService(client=fake).parse_menu(image_bytes=jpeg(), mime_type="image/jpeg")


class RecipeTest(SimpleTestCase):

    def test_haiku_reads_the_recipe_and_the_list_comes_back(self):
        lines = [{"ingredient": "Onion", "quantity_text": "2 medium"}]
        fake = FakeClaude({"ingredients": lines})
        self.assertEqual(AIService(client=fake).parse_recipe(text="2 medium onions"), lines)
        call = fake.calls[0]
        self.assertEqual((call["model"], call["endpoint"]), ("claude-haiku-5-5", "messages"))
        self.assertNotIn("fallbacks", call)
        self.assertEqual(call["output_config"]["format"]["schema"], ai_service.RECIPE_SCHEMA)

    def test_any_failure_is_one_clear_message(self):
        for fake in (FakeClaude(stop_reason="refusal"), FakeClaude().fails_with(Exception("529 overloaded")),
                     FakeClaude(text="not json")):
            with self.assertRaises(Exception) as ctx:
                AIService(client=fake).parse_recipe(text="2 onions")
            self.assertIn("Couldn't read that recipe", str(ctx.exception))

    def test_without_a_client_it_says_the_key_is_missing(self):
        with self.assertRaises(Exception) as ctx:
            AIService(client=None).parse_recipe(text="2 onions")
        self.assertIn("activation key", str(ctx.exception))


class MatchTest(SimpleTestCase):

    def test_matches_come_back_by_name_and_an_invented_name_is_unmatched(self):
        fake = FakeClaude({"matches": [
            {"ingredient": "cream", "inventory_item": "Heavy Cream"},
            {"ingredient": "saffron", "inventory_item": "Gold Dust"},       # not in inventory
        ]})
        result = AIService(client=fake).match_ingredients(["cream", "saffron", "salt"], ["Heavy Cream", "Salt"])
        self.assertEqual(result, {"cream": "Heavy Cream", "saffron": None, "salt": None})
        self.assertEqual(fake.calls[0]["model"], "claude-haiku-5-5")

    def test_a_failure_leaves_everything_unmatched(self):
        fake = FakeClaude().fails_with(Exception("down"))
        self.assertEqual(AIService(client=fake).match_ingredients(["cream"], ["Heavy Cream"]), {"cream": None})


class PricingTest(SimpleTestCase):

    def test_the_suggestion_is_the_text(self):
        fake = FakeClaude(text="Price it at 240.")
        self.assertEqual(AIService(client=fake).suggest_pricing("Dal", "lentils 40"), "Price it at 240.")

    def test_a_refusal_is_no_suggestion(self):
        self.assertIsNone(AIService(client=FakeClaude(text="", stop_reason="refusal")).suggest_pricing("Dal", "x"))
