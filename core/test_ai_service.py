# core/test_ai_service.py
"""
Tests for core.ai_service.AIService's fallback behavior when Gemini is
unavailable (real production case: a sustained 503 from Gemini while a
tenant uploaded a menu PDF -- see the "build our own model" conversation
this fix came out of). The regex fallback parser already existed, but only
ever triggered for pasted text; a PDF/Word/Excel/CSV *upload* had no
fallback at all and just failed outright. This confirms the fix routes
every text-extractable upload type through the same safety net, while a
bare photo (which has no local text to extract) still correctly raises
rather than silently pretending to succeed.
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase

from core.ai_service import AIService


class FallbackTextExtractionDispatchTests(TestCase):
    """_extract_fallback_text must route each upload type to the right
    local extractor, and degrade to "" (not raise) for anything it can't
    get text from -- it's already the last line of defense."""

    def setUp(self):
        self.svc = AIService()

    def test_pdf_mime_routes_to_pdf_extraction(self):
        with patch.object(self.svc, "_extract_pdf_text", return_value="Paneer Tikka 260") as mock_pdf:
            result = self.svc._extract_fallback_text(b"%PDF-1.4 fake", "application/pdf")
        mock_pdf.assert_called_once()
        self.assertEqual(result, "Paneer Tikka 260")

    def test_pdf_magic_bytes_detected_even_with_missing_mime_type(self):
        """Some browsers send no mime type at all -- must still sniff %PDF-."""
        with patch.object(self.svc, "_extract_pdf_text", return_value="Dal Tadka 240") as mock_pdf:
            result = self.svc._extract_fallback_text(b"%PDF-1.4 fake", "")
        mock_pdf.assert_called_once()
        self.assertEqual(result, "Dal Tadka 240")

    def test_docx_mime_routes_to_docx_extraction(self):
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        with patch.object(self.svc, "_extract_docx", return_value="Butter Naan 60") as mock_docx:
            result = self.svc._extract_fallback_text(b"fake docx bytes", docx_mime)
        mock_docx.assert_called_once()
        self.assertEqual(result, "Butter Naan 60")

    def test_xlsx_mime_routes_to_xlsx_extraction(self):
        xlsx_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        with patch.object(self.svc, "_extract_xlsx", return_value="Jeera Rice 180") as mock_xlsx:
            result = self.svc._extract_fallback_text(b"fake xlsx bytes", xlsx_mime)
        mock_xlsx.assert_called_once()
        self.assertEqual(result, "Jeera Rice 180")

    def test_plain_text_mime_decodes_directly(self):
        result = self.svc._extract_fallback_text(b"Masala Tea 80", "text/plain")
        self.assertEqual(result, "Masala Tea 80")

    def test_image_mime_returns_empty_not_an_exception(self):
        """A photo has no local text to extract -- must degrade to "",
        never raise, since this is already the last-resort fallback path."""
        result = self.svc._extract_fallback_text(b"\xff\xd8\xff fake jpeg", "image/jpeg")
        self.assertEqual(result, "")

    def test_extraction_failure_degrades_to_empty_not_a_crash(self):
        """A corrupt/unreadable docx must not blow up the fallback itself."""
        docx_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        with patch.object(self.svc, "_extract_docx", side_effect=Exception("corrupt file")):
            result = self.svc._extract_fallback_text(b"garbage", docx_mime)
        self.assertEqual(result, "")

    def test_huge_extracted_text_is_truncated_not_passed_through_whole(self):
        """The decompression-bomb backstop: a file whose compressed size is
        under the 15MB upload cap but that unpacks internally into far more
        text than any real menu would ever contain must still be bounded
        before it reaches the regex parser."""
        huge_text = "x" * 200_000
        with patch.object(self.svc, "_extract_pdf_text", return_value=huge_text):
            result = self.svc._extract_fallback_text(b"%PDF-1.4 fake", "application/pdf")
        self.assertEqual(len(result), self.svc._MAX_FALLBACK_TEXT_CHARS)

    def test_normal_sized_text_is_not_truncated(self):
        with patch.object(self.svc, "_extract_pdf_text", return_value="Paneer Tikka 260"):
            result = self.svc._extract_fallback_text(b"%PDF-1.4 fake", "application/pdf")
        self.assertEqual(result, "Paneer Tikka 260")


class ParseMenuFallbackWiringTests(TestCase):
    """End-to-end: when Gemini fails on a real upload (not just pasted
    text), parse_menu must now fall back to the regex parser using text
    extracted from that same file, instead of just raising."""

    def setUp(self):
        self.svc = AIService()
        self.svc.client = MagicMock()  # pretend an API key IS configured

    def test_pdf_upload_falls_back_to_manual_parser_when_gemini_fails(self):
        """The exact scenario from tonight's real production logs: PDF
        upload, Gemini returns a sustained 503, must not just fail outright."""
        self.svc.client.models.generate_content.side_effect = Exception(
            "503 UNAVAILABLE: This model is currently experiencing high demand."
        )
        with patch.object(self.svc, "_extract_pdf_text", return_value="Starters\nPaneer Tikka 260"):
            result = self.svc.parse_menu(image_bytes=b"%PDF-1.4 fake", mime_type="application/pdf")

        self.assertTrue(len(result) > 0)
        self.assertEqual(result[0]["items"][0]["name"], "Paneer Tikka")
        self.assertEqual(result[0]["items"][0]["price"], 260.0)

    def test_photo_upload_still_raises_when_gemini_fails_no_fallback_possible(self):
        """Must NOT silently pretend a photo parsed -- there's no local text
        to extract from an image, so this has to surface the real failure."""
        self.svc.client.models.generate_content.side_effect = Exception("503 UNAVAILABLE")
        with self.assertRaises(Exception):
            self.svc.parse_menu(image_bytes=b"\xff\xd8\xff fake jpeg", mime_type="image/jpeg")

    def test_no_api_key_configured_now_works_for_a_pdf_upload_too(self):
        """Previously: no API key + any file upload (even a plain-text-
        extractable one like a PDF) raised "requires an AI activation key" --
        wrong, since PDF/Word/Excel/CSV don't actually need AI at all if
        local extraction succeeds. Only a real photo genuinely needs it."""
        self.svc.client = None
        with patch.object(self.svc, "_extract_pdf_text", return_value="Rice\nJeera Rice 180"):
            result = self.svc.parse_menu(image_bytes=b"%PDF-1.4 fake", mime_type="application/pdf")

        self.assertEqual(result[0]["items"][0]["name"], "Jeera Rice")

    def test_no_api_key_configured_and_a_photo_still_raises_clearly(self):
        self.svc.client = None
        with self.assertRaises(Exception) as ctx:
            self.svc.parse_menu(image_bytes=b"\xff\xd8\xff fake jpeg", mime_type="image/jpeg")
        self.assertIn("activation key", str(ctx.exception))


class ManualParserFlattenedTableTests(TestCase):
    """Found via real-data testing against an actual restaurant's exported
    menu PDF: pypdf flattens each table row ("Category | Dish | Price")
    into one line, "Category Dish Price", instead of a separate category
    header followed by item lines. The regex parser must detect this shape
    (via the column-header row as positive evidence) and split the leading
    category phrase off each item name -- and must NOT do this for an
    ordinary menu with no such header row, where a repeated leading word
    could just be a real ingredient shared by two different dishes."""

    def setUp(self):
        self.svc = AIService()

    def test_flattened_table_with_header_row_splits_category_correctly(self):
        text = (
            "Category Dish Price (Rs.)\n"
            "Starters Veg Spring Rolls 180\n"
            "Starters Paneer Tikka 260\n"
            "Starters Chicken Wings 320\n"
            "Main Course Chicken Biryani 380\n"
            "Main Course Butter Chicken 420\n"
        )
        result = self.svc._manual_text_parse(text)
        by_cat = {entry["category"]: entry["items"] for entry in result}

        self.assertIn("Starters", by_cat)
        self.assertIn("Main Course", by_cat)
        starter_names = {item["name"] for item in by_cat["Starters"]}
        self.assertEqual(starter_names, {"Veg Spring Rolls", "Paneer Tikka", "Chicken Wings"})
        # The header row itself must never become a bogus category.
        self.assertNotIn("Category Dish Price (Rs.)", by_cat)

        main_course_items = {item["name"]: item["is_veg"] for item in by_cat["Main Course"]}
        self.assertFalse(main_course_items["Chicken Biryani"])
        self.assertFalse(main_course_items["Butter Chicken"])

    def test_ordinary_newline_separated_format_is_unaffected(self):
        """Regression check: the original, more common shape -- a category
        on its own line, followed by "Item Price" lines -- must parse
        exactly as it always did. No header row here, so inline-category
        detection must never activate."""
        text = "APPETIZERS\nSpring Rolls 120\nPaneer Tikka 250\nMAIN COURSE\nButter Chicken 380"
        result = self.svc._manual_text_parse(text)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["category"], "Appetizers")
        self.assertEqual({i["name"] for i in result[0]["items"]}, {"Spring Rolls", "Paneer Tikka"})
        self.assertEqual(result[1]["category"], "Main Course")
        self.assertEqual(result[1]["items"][0]["name"], "Butter Chicken")

    def test_repeated_leading_word_without_a_header_row_is_not_mistaken_for_a_category(self):
        """The specific false-positive this design deliberately avoids:
        two dishes sharing a leading word ("Chicken") is not, by itself,
        evidence of a flattened table -- without a header row, it must stay
        exactly as typed."""
        text = "STARTERS\nChicken Biryani 380\nChicken 65 320"
        result = self.svc._manual_text_parse(text)

        self.assertEqual(len(result), 1)
        names = {i["name"] for i in result[0]["items"]}
        self.assertEqual(names, {"Chicken Biryani", "Chicken 65"})

    def test_looks_like_table_header_requires_two_or_more_column_words(self):
        self.assertTrue(self.svc._looks_like_table_header("Category Dish Price"))
        self.assertTrue(self.svc._looks_like_table_header("Item Name and Price"))
        self.assertFalse(self.svc._looks_like_table_header("Chicken Biryani"))  # just a dish
        self.assertFalse(self.svc._looks_like_table_header("Price 380"))  # has a digit, not a header
