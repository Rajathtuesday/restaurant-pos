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
# An import or export-from statement in a built module: it starts a line,
# "from" follows a space or a brace, and the address is relative. (A looser
# finder read the field id "f-from" in setup/promo_form.js as an import.)
IMPORT_RE = re.compile(r"""(?m)^(?:import|export)\b[^;]*?[\s}]from\s*["']([./][^"']*)["']""")
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
    """Every module Rasova ships: all of static/js but the vendored copies
    and the node tests. (Until 21 Nov this was core/, billing/, pages/ and
    four top-level files; the page modules moved into a folder each in
    November, none of them was checked, and reports/dashboard.js shipped an
    import of app.<hash>.<hash>.js unseen.)"""
    js = BASE / "static" / "js"
    for path in sorted(js.rglob("*.js")):
        if path.relative_to(js).parts[0] not in ("vendor", "tests"):
            yield path


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
                self.assertNotRegex(
                    target, r"\.[0-9a-f]{12}\.[0-9a-f]{12}\.js$",
                    f"{rel}: import of {target!r} was rewritten twice (see core/storage.py)",
                )
        self.assertGreater(checked, 100, "too few imports were checked; did the modules move?")

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


class NoWindowGlobalsTest(SimpleTestCase):
    """Since 15 Nov 2026 every page is a module that imports what it needs
    (core/app.js exports the shared objects). The old window.* bridges
    (window.ui, window.apiClient, window.OrderEdit, window.RasovaOffers...)
    are gone; a new one would let a page lean on load order again."""

    ASSIGN = re.compile(r"\b(?:window|globalThis)\.([A-Za-z_$][\w$]*)\s*=(?!=)")

    def test_no_script_publishes_on_window(self):
        found = []
        for path in Path(settings.BASE_DIR, "static", "js").rglob("*.js"):
            if "vendor" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for match in self.ASSIGN.finditer(text):
                line = text[:match.start()].count(chr(10)) + 1
                found.append(f"{path.relative_to(settings.BASE_DIR)}:{line} window.{match.group(1)}")
        self.assertEqual(found, [], "import from core/app.js instead of publishing on window")

    def test_the_check_sees_an_assignment(self):
        self.assertTrue(self.ASSIGN.search("window.ui = ui;"))
        self.assertFalse(self.ASSIGN.search("if (window.ui === x) {}"))


class ImportRewritePatternsTest(SimpleTestCase):
    """RasovaStaticStorage's own import patterns (core/storage.py), run in
    order the way Django runs them: every address in a statement is
    rewritten exactly once, and prose in a comment is never a statement.
    Django's patterns rewrote reports/dashboard.js's import of core/app.js
    twice because its header said "the Export menu," (21 Nov 2026)."""

    SOURCE = (
        "/*\n"
        " * The sales report: the hourly line, and the Export menu,\n"
        " * whose links carry the filters. We import the theme, and\n"
        " * export nothing from here.\n"
        " */\n"
        'import { theme } from "../core/app.js";\n'
        "import {\n"
        "  delegate,\n"
        "  onReady,\n"
        '} from "../core/dom.js";\n'
        'import * as money from "../core/money.js";\n'
        'import pageData from "../core/page_data.js";\n'
        'import "./side_effect.js";\n'
        'export { chartColours } from "./dashboard_parts.js";\n'
        'export * from "./more_parts.js";\n'
        "const later = () => import(\"./lazy.js\");\n"
        'const label = "Import from \\"../core/nothing.js\\";";\n'
        "export { theme, later };\n"
    )
    ADDRESSES = ["../core/app.js", "../core/dom.js", "../core/money.js", "../core/page_data.js",
                 "./side_effect.js", "./dashboard_parts.js", "./more_parts.js", "./lazy.js"]

    def rewrite(self, text):
        from core.storage import RasovaStaticStorage
        seen = []

        def converter(template):
            def convert(match):
                found = match.groupdict()
                seen.append(found["url"])
                found["url"] = found["url"].replace(".js", ".0123456789ab.js")
                return template % found
            return convert

        for pattern, template in RasovaStaticStorage()._patterns["*.js"]:
            if "import" in pattern.pattern or "export" in pattern.pattern:
                text = pattern.sub(converter(template), text)
        return text, seen

    def test_each_address_is_rewritten_once_and_prose_never(self):
        text, seen = self.rewrite(self.SOURCE)
        self.assertEqual(sorted(seen), sorted(self.ADDRESSES))
        for address in self.ADDRESSES:
            self.assertIn(address.replace(".js", ".0123456789ab.js"), text)
        self.assertNotIn(".0123456789ab.0123456789ab.js", text)
        self.assertIn("the Export menu,", text)
        self.assertIn('"Import from \\"../core/nothing.js\\";"', text)

    def test_django_own_patterns_fall_into_the_trap(self):
        """Why core/storage.py has its own: the same source through Django's
        patterns rewrites core/app.js twice."""
        from django.contrib.staticfiles.storage import HashedFilesMixin
        seen = []
        text = self.SOURCE
        for raw, template in HashedFilesMixin._js_module_import_aggregation_patterns[1]:
            pattern = re.compile(raw, re.IGNORECASE)

            def convert(match, template=template):
                found = match.groupdict()
                seen.append(found["url"])
                found["url"] = found["url"].replace(".js", ".0123456789ab.js")
                return template % found
            text = pattern.sub(convert, text)
        self.assertGreater(seen.count("../core/app.0123456789ab.js"), 0)
