"""
People can pinch to zoom on every page (V13, 13 Nov 2026).

base.html, the guest QR menu and login set maximum-scale=1 (and two of them
user-scalable=no), which blocks pinch zoom for anyone with weak eyesight, on
every page. The reason it was there, iPhone Safari zooming into inputs with
text under 16px, is handled in static/css/rasova.css instead.

Run: python manage.py test core.tests.test_page_zoom
"""
import re
import subprocess

from django.conf import settings
from django.test import SimpleTestCase

BLOCKS_ZOOM = re.compile(r'<meta[^>]+name="viewport"[^>]*(user-scalable\s*=\s*(no|0)|maximum-scale\s*=\s*1(\.0)?\b)', re.I)


class PinchZoomTest(SimpleTestCase):

    def test_no_page_blocks_pinch_zoom(self):
        files = subprocess.run(["git", "ls-files", "*.html"], capture_output=True, text=True,
                               cwd=settings.BASE_DIR).stdout.split()
        blocking = [f for f in files if not f.startswith("md_files/")
                    and BLOCKS_ZOOM.search((settings.BASE_DIR / f).read_text(encoding="utf-8", errors="ignore"))]
        self.assertEqual(blocking, [])

    def test_iphone_inputs_stay_at_16px(self):
        css = (settings.BASE_DIR / "static" / "css" / "rasova.css").read_text(encoding="utf-8")
        self.assertIn("@supports (-webkit-touch-callout: none)", css)
        self.assertIn("font-size: max(16px, 1em)", css)
