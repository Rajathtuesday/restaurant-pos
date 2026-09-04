#!/usr/bin/env python
"""
Real-data smoke test for the AI menu import fallback pipeline (no mocks,
no Gemini API call). Runs the exact local-extraction + regex-parse path
that fires when Gemini is unavailable: real pypdf/python-docx/openpyxl
extraction -> real _manual_text_parse -> real _guess_veg classification.

This is deliberately separate from the unit tests in core/test_ai_service.py
and menu/tests.py (which mock the extractors to test the WIRING in
isolation). This script proves the wiring plus the real libraries plus a
real file all actually agree with each other on real menu data.

Usage:
    python scripts/test_menu_import_fallback.py "C:\\path\\to\\menu.pdf"
    python scripts/test_menu_import_fallback.py "C:\\path\\to\\menu.xlsx"
"""
import os
import sys
import mimetypes

# Windows consoles often default to cp1252, which can't print a literal Rs.
# sign or other non-ASCII characters straight out of a real menu file --
# replace rather than crash, since this is a diagnostic script, not the app.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django
django.setup()

from core.ai_service import AIService


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/test_menu_import_fallback.py <path-to-menu-file>")

    path = sys.argv[1]
    if not os.path.isfile(path):
        sys.exit(f"File not found: {path}")

    with open(path, "rb") as f:
        file_bytes = f.read()

    mime_type, _ = mimetypes.guess_type(path)
    print(f"[1] File: {path}")
    print(f"    Size: {len(file_bytes):,} bytes")
    print(f"    Guessed mime type: {mime_type}")

    svc = AIService()

    print("\n[2] Extracting text locally (real pypdf/python-docx/openpyxl, no Gemini)...")
    extracted = svc._extract_fallback_text(file_bytes, mime_type)
    if not extracted:
        sys.exit("    FAILED: no text extracted -- either an unsupported file type, "
                  "a scanned/image-only PDF with no text layer, or a real parsing bug.")
    print(f"    Extracted {len(extracted)} characters.")
    print("    --- first 200 chars ---")
    print("    " + extracted[:200].replace("\n", "\n    "))

    print("\n[3] Running the real regex parser + veg/non-veg guesser (no Gemini)...")
    structured = svc._manual_text_parse(extracted)
    if not structured:
        sys.exit("    FAILED: parser found zero items.")

    total_items = 0
    veg_count = 0
    non_veg_count = 0
    print(f"\n[4] Result: {len(structured)} categories found\n")
    for entry in structured:
        cat = entry.get("category", "?")
        items = entry.get("items", [])
        print(f"  {cat}")
        for item in items:
            total_items += 1
            is_veg = item.get("is_veg", True)
            veg_count += is_veg
            non_veg_count += not is_veg
            tag = "VEG    " if is_veg else "NON-VEG"
            print(f"    [{tag}] {item['name']:<35} Rs.{item['price']}")
        print()

    print("-" * 60)
    print(f"TOTAL: {total_items} items  |  {veg_count} veg  |  {non_veg_count} non-veg")
    print("-" * 60)
    print("\nCheck the tags above against the real menu by eye -- this is the whole point")
    print("of a real-data run: confirm the classification actually looks right, not just")
    print("that the code runs without crashing.")


if __name__ == "__main__":
    main()
