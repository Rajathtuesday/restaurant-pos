"""
The static files, as production serves them.

Production uses core.storage.RasovaStaticStorage: every file gets a hashed
name, and the relative imports inside the JavaScript modules are rewritten
to the hashed names so a module loaded as app.<hash>.js still finds
http.<hash>.js. That rewriting is regex based. This collects the static
files with that storage into a temporary directory, the way deploy.sh
does, and checks:

  * every import and export-from inside every collected module names a
    file that exists in the manifest (a form the regex missed, or a typo
    in a path, fails here and not in a browser);
  * every module Rasova ships parses in node (a syntax error in a module
    would otherwise only show up when a page loads it);
  * no template references a third-party host for a script, a stylesheet
    or a font: Rasova serves its own (static/vendor, VENDOR.md), so a bill
    prints when the restaurant's internet is slow or gone;
  * every {% static 'path' %} written literally in a template points at a
    file that exists.

Run: python manage.py test core.tests.test_static_modules
"""
import json
import re
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from core.testing import not_in_compiled_build
from orders.tests.test_cart_tax_js import NODE

BASE = Path(settings.BASE_DIR)
MODULE_DIRS = [BASE / "static" / "js" / "core", BASE / "static" / "js" / "billing", BASE / "static" / "js" / "pages"]
# The engines and the shared order controls live at the top of static/js.
TOP_LEVEL_MODULES = ["cart_tax.js", "offers.js", "order_edit.js", "approval.js"]
IMPORT_RE = re.compile(r"""(?:^|\n)\s*(?:import|export)\b[^;]*?\bfrom\s*["']([^"']+)["']""")
# A known CDN anywhere, or anything a page LOADS from another host: a script,
# an image, a stylesheet, a CSS url(). (Two setup pages fetched their QR codes
# from api.qrserver.com, handing it every table's private menu address; the
# CDN list alone did not see it, 13 Nov 2026.) A link the user follows is fine.
THIRD_PARTY = re.compile(
    r"https?://(cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com|fonts\.googleapis\.com|fonts\.gstatic\.com|code\.jquery\.com)"
    r"|(?:\bsrc|\bsrcset)\s*=\s*[\"']https?://"
    r"|<link\b[^>]*\bhref\s*=\s*[\"']https?://"
    r"|url\(\s*[\"']?https?://")
STATIC_TAG = re.compile(r"""{%\s*static\s+["']([^"'{}]+)["']\s*%}""")


def app_templates():
    """Every template Rasova renders (not the venv, not the notes)."""
    skip = ("\\.venv\\", "/.venv/", "md_files", "node_modules", "staticfiles", "rasova_android", ".code-review-graph", "graft")
    for path in BASE.rglob("*.html"):
        text = str(path)
        if any(s in text for s in skip):
            continue
        if path.parts[len(BASE.parts)] in ("public", "js", "docs"):
            continue
        yield path


def own_modules():
    for name in TOP_LEVEL_MODULES:
        yield BASE / "static" / "js" / name
    for folder in MODULE_DIRS:
        if folder.exists():
            yield from sorted(folder.glob("*.js"))


class StaticModulesTest(SimpleTestCase):
    """Collects once for the class: collectstatic copies the vendor files too."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        storages = dict(settings.STORAGES)
        storages["staticfiles"] = {"BACKEND": "core.storage.RasovaStaticStorage"}
        with override_settings(STATIC_ROOT=str(cls.root), STORAGES=storages, WHITENOISE_KEEP_ONLY_HASHED_FILES=False):
            call_command("collectstatic", interactive=False, verbosity=0)
        cls.manifest = json.loads((cls.root / "staticfiles.json").read_text(encoding="utf-8"))["paths"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        super().tearDownClass()

    def test_every_import_in_every_collected_module_resolves(self):
        checked = 0
        for source in own_modules():
            rel = source.relative_to(BASE / "static").as_posix()
            hashed = self.manifest.get(rel)
            self.assertIsNotNone(hashed, f"{rel} is not in the manifest")
            text = (self.root / hashed).read_text(encoding="utf-8")
            for target in IMPORT_RE.findall(text):
                checked += 1
                resolved = (Path(hashed).parent / target).as_posix()
                resolved = re.sub(r"[^/]+/\.\./", "", resolved)
                self.assertTrue(
                    (self.root / resolved).exists(),
                    f"{rel}: import of {target!r} became {resolved!r}, which does not exist after collectstatic",
                )
                self.assertRegex(
                    target, r"\.[0-9a-f]{12}\.js$",
                    f"{rel}: import of {target!r} was not rewritten to a hashed name; "
                    "is RasovaStaticStorage.support_js_module_import_aggregation still on?",
                )
        self.assertGreater(checked, 5, "no imports were checked; did the modules move?")

    def test_the_modules_parse(self):
        if not NODE:
            self.skipTest("node is not installed")
        for source in own_modules():
            done = subprocess.run([NODE, "--check", str(source)], capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, f"{source.name} does not parse:\n{done.stderr[-2000:]}")


class TemplateStaticReferencesTest(SimpleTestCase):
    @not_in_compiled_build("the templates' source")
    def test_every_literal_static_path_exists(self):
        missing = []
        for path in app_templates():
            for ref in STATIC_TAG.findall(path.read_text(encoding="utf-8", errors="ignore")):
                if ref.endswith("/"):
                    continue    # a directory prefix the template completes at render time
                if finders.find(ref) is None:
                    missing.append(f"{path.relative_to(BASE)}: {ref}")
        self.assertEqual(missing, [], "templates reference static files that do not exist:\n" + "\n".join(missing))

    @not_in_compiled_build("the templates' source")
    def test_no_template_loads_anything_from_a_third_party_host(self):
        from core.tests.test_frontend_hygiene import BASELINE
        offenders = []
        for path in app_templates():
            rel = path.relative_to(BASE).as_posix()
            hits = len(THIRD_PARTY.findall(path.read_text(encoding="utf-8", errors="ignore")))
            allowed = BASELINE.get(rel, {}).get("third_party", 0)
            if hits > allowed:
                offenders.append(f"{rel}: {hits} (allowed {allowed})")
        self.assertEqual(offenders, [], "third-party hosts in templates:\n" + "\n".join(offenders))
