"""
What a PDF may read while WeasyPrint renders it (core/pdf.py).

Why: since 12 November 2026 bill.html links its stylesheets instead of
carrying them inline. WeasyPrint fetched linked files over HTTP from the
address it was given, the server's own public address, so the bill PDF
went out through Cloudflare and back to read the server's own files, and
came out unstyled when that failed. It would also have fetched any
address a page named. Now static files come from disk and anything that
is not Rasova's own, or the restaurant's logo, is refused.

The rules are tested on every machine. The full PDF render is tested only
where WeasyPrint's system libraries are installed (the server, CI); a
Windows laptop has not got them.

Run: python manage.py test core.tests.test_pdf
"""
import tempfile
from pathlib import Path


from django.conf import settings
from django.test import SimpleTestCase, override_settings

from core.pdf import resolve

def _have_weasyprint():
    """The real WeasyPrint, not the stand-in billing/tests.py and
    inventory's tests put in sys.modules where the system libraries are
    missing (a bare module whose HTML is a lambda). The real HTML is a
    class; that tells them apart, whichever test file loaded first in this
    process. (Asking for a submodule does not: the failed real import can
    leave weasyprint.urls loaded behind the stand-in.)"""
    try:
        import weasyprint
    except (ImportError, OSError):
        return False
    return isinstance(getattr(weasyprint, "HTML", None), type)

STATIC = Path(settings.BASE_DIR) / "static"


class WhatAPdfMayReadTest(SimpleTestCase):

    def test_static_files_come_from_disk_whatever_host_the_link_names(self):
        for url in ("https://rasova.net/static/css/bill.css",
                    "http://localhost:8000/static/css/bill.css",
                    "/static/css/bill.css"):
            with self.subTest(url=url):
                kind, path = resolve(url)
                self.assertEqual(kind, "file")
                self.assertEqual(Path(path), (STATIC / "css" / "bill.css").resolve())

    def test_the_paper_width_sheets_and_the_vendor_files_are_found(self):
        for name in ("css/print/paper_58.css", "css/print/paper_80.css",
                     "vendor/bootstrap/bootstrap.min.css", "css/themes/luxury.css"):
            with self.subTest(name=name):
                self.assertEqual(resolve("https://rasova.net/static/" + name)[0], "file")

    def test_a_collected_hashed_file_is_read_from_static_root(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "css").mkdir()
            hashed = Path(root) / "css" / "bill.0123456789ab.css"
            hashed.write_text("/* collected */", encoding="utf-8")
            with override_settings(STATIC_ROOT=root):
                kind, path = resolve("https://rasova.net/static/css/bill.0123456789ab.css")
        self.assertEqual((kind, Path(path)), ("file", hashed.resolve()))

    def test_nothing_outside_the_static_folders_can_be_read(self):
        for url in ("https://rasova.net/static/../core/settings.py",
                    "https://rasova.net/static/%2e%2e/core/settings.py",
                    "https://rasova.net/static/css/no_such_file.css"):
            with self.subTest(url=url):
                self.assertEqual(resolve(url)[0], "refuse")

    def test_any_other_address_is_refused(self):
        for url in ("https://rasova.net/dashboard/",
                    "http://169.254.169.254/latest/meta-data/",
                    "http://localhost:8000/admin/",
                    "file:///etc/passwd",
                    "https://evil.example/static/css/bill.css.png"):
            with self.subTest(url=url):
                kind, why = resolve(url)
                if url.endswith(".png"):
                    # a static path on another host is still only ever read from our own disk
                    self.assertEqual(kind, "refuse")
                else:
                    self.assertEqual(kind, "refuse", why)

    @override_settings(MEDIA_URL="https://media.rasova.net/")
    def test_a_logo_on_r2_is_fetched_from_its_own_host_only(self):
        self.assertEqual(resolve("https://media.rasova.net/logos/spice.png"), ("network", "https://media.rasova.net/logos/spice.png"))
        self.assertEqual(resolve("https://media.rasova.net.evil.example/logos/spice.png")[0], "refuse")

    def test_a_logo_stored_locally_is_read_from_media_root(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "logos").mkdir()
            logo = Path(root) / "logos" / "spice.png"
            logo.write_bytes(b"\x89PNG")
            with override_settings(MEDIA_URL="/media/", MEDIA_ROOT=root):
                kind, path = resolve("https://rasova.net/media/logos/spice.png")
                escape = resolve("https://rasova.net/media/../core/settings.py")[0]
        self.assertEqual((kind, Path(path)), ("file", logo.resolve()))
        self.assertEqual(escape, "refuse")

    def test_data_uris_carry_their_own_content(self):
        self.assertEqual(resolve("data:image/png;base64,AAAA")[0], "data")


class RenderPdfTest(SimpleTestCase):

    def setUp(self):
        # Asked when the test runs, not when the file is imported: the
        # stand-in may be put in place by a test file loaded later.
        if not _have_weasyprint():
            self.skipTest("WeasyPrint's system libraries are not installed here")

    def test_a_linked_stylesheet_is_applied_without_any_network(self):
        from unittest.mock import patch

        from core.pdf import render_pdf
        page = ('<html><head><link rel="stylesheet" href="https://unreachable.invalid/static/css/print/paper_58.css">'
                '</head><body><p>Bill</p></body></html>')
        # the network is not there at all: any HTTP fetch would fail the render
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("went to the network")):
            pdf = render_pdf(page, base_url="https://unreachable.invalid/")
        self.assertTrue(pdf.startswith(b"%PDF"))
        # paper_58.css sets the page 58 mm wide: 58 mm is about 164.4 points
        self.assertIn(b"164.4", pdf.replace(b" ", b" "))
