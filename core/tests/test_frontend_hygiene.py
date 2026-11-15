"""
The ratchet: templates can only get cleaner.

For every template this counts
  script       lines inside inline <script> blocks (the one boot script in
               base.html, marked data-rasova-boot, and json_script blocks
               do not count: they are the two inline forms Rasova allows)
  style        lines inside inline <style> blocks
  handlers     inline event handlers: onclick=, onchange=, onsubmit=, any on...=
               (15 Nov 2026: it counted onclick only, and onchange and onsubmit
               handlers, a confirm() among them, sat unseen in templates)
  third_party  references to a CDN or Google Fonts host
  globals      lines at the top level of a page script that read one of
               the shared objects (apiClient, ui, offlineQueue, ...). Those
               come from core/app.js, a module, which runs after the page
               is parsed; a top-level read runs before it and finds nothing.
               Inside a function or a DOMContentLoaded handler is fine.

BASELINE holds the numbers on the day the ratchet was set. A file may not
get worse than its entry; and an entry may not be looser than the file now
is, so when a page is migrated its entry must be lowered (or deleted when
every count is zero). Each step of the frontend plan shrinks BASELINE, and
when it is empty this test keeps it empty: a new page that puts code
inline fails here, with the numbers, before it reaches anyone.

To refresh the numbers after migrating pages (it prints the dictionary):
    python -m core.tests.test_frontend_hygiene

Run: python manage.py test core.tests.test_frontend_hygiene
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.testing import not_in_compiled_build

BASE = Path(settings.BASE_DIR)

SCRIPT_OPEN = re.compile(r"<script\b([^>]*)>", re.I)
SCRIPT_CLOSE = re.compile(r"</script\s*>", re.I)
STYLE_OPEN = re.compile(r"<style\b[^>]*>", re.I)
STYLE_CLOSE = re.compile(r"</style\s*>", re.I)
THIRD_PARTY = re.compile(r"https?://(cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com|fonts\.googleapis\.com|fonts\.gstatic\.com|code\.jquery\.com)")
SHARED_GLOBALS = re.compile(r"\b(apiClient|ui\.(toast|confirm|showLoading|hideLoading)|themeManager|offlineQueue|offlinePaymentQueue|rasovaPlatform|refreshNotifications|updateOfflineBanner|RasovaCartTax|RasovaOffers|OrderEdit|RasovaApproval)\b")
TOP_LEVEL_SKIP = re.compile(r"^\s*(//|/\*|\*)")   # a comment mentioning a global is not a read

SKIP_PARTS = {"md_files", "node_modules", "staticfiles", "rasova_android", "public", "js", "docs", ".venv", ".code-review-graph", "graft"}


def app_templates():
    for path in sorted(BASE.rglob("*.html")):
        rel = path.relative_to(BASE)
        if rel.parts[0] in SKIP_PARTS or any(p in SKIP_PARTS for p in rel.parts[:-1]):
            continue
        yield path


# An inline event handler: an on...= attribute inside a tag.
INLINE_HANDLER = re.compile(r"<[a-zA-Z][^>]*?\son[a-z]+\s*=", re.I)


def measure(text):
    """The five counts for one template's text."""
    script = style = globals_ = 0
    handlers = len(INLINE_HANDLER.findall(text))
    third_party = len(THIRD_PARTY.findall(text))

    in_script = in_style = False
    allowed_block = False
    depth = 0
    for line in text.splitlines():
        if in_script:
            if SCRIPT_CLOSE.search(line):
                in_script = False
                continue
            if allowed_block:
                continue
            script += 1
            # A line that defines a function (=> or function) runs later, not now.
            lazy = "=>" in line or "function" in line
            if depth == 0 and not lazy and SHARED_GLOBALS.search(line) and not TOP_LEVEL_SKIP.match(line):
                globals_ += 1
            depth += line.count("{") - line.count("}")
            if depth < 0:
                depth = 0
            continue
        if in_style:
            if STYLE_CLOSE.search(line):
                in_style = False
                continue
            style += 1
            continue
        opened = SCRIPT_OPEN.search(line)
        if opened and not SCRIPT_CLOSE.search(line[opened.end():]):
            attrs = opened.group(1)
            # src= scripts have no body; json_script and the boot script are
            # the two inline forms that are allowed (structured data for
            # search engines, application/ld+json, is data too).
            allowed_block = ("src=" in attrs or "application/json" in attrs or "application/ld+json" in attrs
                             or "data-rasova-boot" in attrs)
            in_script = True
            depth = 0
            continue
        if (opened and "src=" not in opened.group(1) and "application/json" not in opened.group(1)
                and "application/ld+json" not in opened.group(1) and "data-rasova-boot" not in opened.group(1)):
            script += 1      # a one-line inline script
        if STYLE_OPEN.search(line) and not STYLE_CLOSE.search(line):
            in_style = True
    return {"script": script, "style": style, "handlers": handlers, "third_party": third_party, "globals": globals_}


def measure_all():
    found = {}
    for path in app_templates():
        counts = measure(path.read_text(encoding="utf-8", errors="ignore"))
        if any(counts.values()):
            found[path.relative_to(BASE).as_posix()] = counts
    return found


# The numbers on 11 November 2026, when the ratchet was set. Only ever lowered.
BASELINE = {
}


class FrontendHygieneTest(SimpleTestCase):
    @not_in_compiled_build("the templates' source")
    def test_templates_only_get_cleaner(self):
        now = measure_all()
        problems = []
        for rel, counts in sorted(now.items()):
            allowed = BASELINE.get(rel)
            if allowed is None:
                problems.append(f"{rel}: has inline code and is not in BASELINE: {counts}")
                continue
            for key, value in counts.items():
                if value > allowed.get(key, 0):
                    problems.append(f"{rel}: {key} went up to {value} (allowed {allowed.get(key, 0)}); move it to static/")
        for rel, allowed in sorted(BASELINE.items()):
            counts = now.get(rel, {})
            for key, value in allowed.items():
                if value > counts.get(key, 0):
                    problems.append(
                        f"{rel}: BASELINE allows {key}={value} but the file has {counts.get(key, 0)}; "
                        "lower the entry (python -m core.tests.test_frontend_hygiene --write)"
                    )
            if not (BASE / rel).exists():
                problems.append(f"{rel}: in BASELINE but the file is gone; delete the entry")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_no_template_has_a_long_dash(self):
        # V14 (14 Nov 2026): Rasova writes no em dash anywhere. All 45 templates
        # that had one were cleared; this keeps it that way, the marketing site too.
        found = [str(p.relative_to(BASE)) for p in sorted(BASE.rglob("*.html"))
                 if not (set(p.relative_to(BASE).parts[:-1]) & (SKIP_PARTS - {"public"}))
                 and "\u2014" in p.read_text(encoding="utf-8")]
        self.assertEqual(found, [])

    def test_measure_counts_what_it_says(self):
        text = (
            '<script src="x.js"></script>\n'
            '<script type="application/json" id="d">{"a": 1}</script>\n'
            "<script data-rasova-boot>boot();</script>\n"
            "<script>\nconst CSRF = apiClient.getCsrf();\nfunction f() {\n  ui.toast('x');\n}\n</script>\n"
            "<style>\na {}\nb {}\n</style>\n"
            '<button onclick="f()">x</button>\n'
            '<form onsubmit="return confirm(1)"><select ONCHANGE="g()"></select></form>\n'
            '<a data-action="go" content="x">not a handler</a>\n'
            '<link href="https://fonts.googleapis.com/css2?family=Outfit">\n'
        )
        self.assertEqual(
            measure(text),
            {"script": 4, "style": 2, "handlers": 3, "third_party": 1, "globals": 1},
        )


if __name__ == "__main__":
    import json
    import sys

    import django

    django.setup()
    data = measure_all()
    text = "BASELINE = {\n" + "".join(
        f"    {rel!r}: {json.dumps(counts)},\n" for rel, counts in sorted(data.items())
    ) + "}\n"
    if "--write" in sys.argv:
        path = Path(__file__)
        source = path.read_text(encoding="utf-8")
        start = source.index("BASELINE = {")
        end = source.index("}\n", start) + 2
        path.write_text(source[:start] + text + source[end:], encoding="utf-8")
        print(f"wrote {len(data)} entries to {path.name}")
    else:
        print(text)
