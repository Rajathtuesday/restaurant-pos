"""
Every picture in a template says what it is (alt), for a screen reader and
for a picture that does not load.

Why (26 Nov 2026, item 12 of md_files/rasova_fault_fix_plan_2026-11-26.md):
onboarding's logo preview had none, on both of its branches. Pictures made
by scripts set theirs (static/js/bill/razorpay_qr.js, static/js/setup/
outlet_settings.js).

Run: python manage.py test core.tests.test_img_alt
"""
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.testing import not_in_compiled_build

ROOT = Path(settings.BASE_DIR)


@not_in_compiled_build("reads the templates from the repository")
class EveryImageHasAltTest(SimpleTestCase):

    def test_every_template_image_has_alt(self):
        files = subprocess.run(["git", "ls-files", "*.html"], capture_output=True, text=True, cwd=ROOT).stdout.split()
        found = []
        for path in files:
            if path.startswith("md_files/") or "/templates/" not in f"/{path}":
                continue
            text = (ROOT / path).read_text(encoding="utf-8")
            for tag in re.finditer(r"<img\b[^>]*>", text, re.S):
                if not re.search(r"\balt\s*=", tag.group(0)):
                    found.append(f"{path}:{text[:tag.start()].count(chr(10)) + 1}")
        self.assertEqual(found, [])
