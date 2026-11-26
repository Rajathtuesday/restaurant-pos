"""
No em dash where a person can read it (CLAUDE.md: none anywhere).

Why (26 Nov 2026, item 11 of md_files/rasova_fault_fix_plan_2026-11-26.md):
templates and scripts were clean, but 116 Python strings still had one,
some on a screen (the guest's phone: "Order confirmed", then a dash,
"sending to the kitchen"), some in the GST export a CA opens, the invoice
email's subject, choice labels and log lines; and four templates wrote one
as the &mdash; entity (the inspection report, an invoice, the QR codes page,
the inventory report). The character is written here as an escape, so this
file keeps the rule too.

The browser smoke walk (core/tests/test_browser_smoke_walk.py) also fails a
page whose words on screen hold one.

Run: python manage.py test core.tests.test_no_em_dash
"""
import ast
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.testing import not_in_compiled_build

DASH = "\u2014"
ROOT = Path(settings.BASE_DIR)


def tracked(*patterns):
    out = subprocess.run(["git", "ls-files", *patterns], capture_output=True, text=True, cwd=ROOT).stdout.split()
    return [f for f in out if not f.startswith("md_files/")]


def app_python(path):
    name = path.split("/")[-1]
    return not ("/tests/" in path or path.startswith("tests/") or "/migrations/" in path or name.startswith("test"))


def docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


@not_in_compiled_build("reads the .py source")
class NoEmDashOnScreenTest(SimpleTestCase):

    def test_no_string_in_the_app_holds_one(self):
        found = []
        for path in tracked("*.py"):
            if not app_python(path):
                continue
            source = (ROOT / path).read_text(encoding="utf-8")
            if DASH not in source:
                continue
            tree = ast.parse(source)
            skip = docstrings(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and DASH in node.value and id(node) not in skip:
                    found.append(f"{path}:{node.lineno}")
        self.assertEqual(found, [], "strings with an em dash (a colon, a comma or a full stop reads better)")

    def test_no_tracked_file_holds_one(self):
        # Phase B (the owner's decision D4, 26 Nov 2026): comments,
        # docstrings, scripts, config, the Android app, docs. Third-party
        # files Rasova ships unchanged (static/vendor) are theirs.
        binary = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".woff", ".woff2", ".ttf", ".otf",
                  ".eot", ".mp3", ".mp4", ".zip", ".apk", ".jar", ".keystore", ".jks", ".db", ".sqlite3", ".xlsx")
        found = []
        for path in tracked():
            if path.startswith("static/vendor/") or path.lower().endswith(binary):
                continue
            try:
                text = (ROOT / path).read_text(encoding="utf-8")
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            if DASH in text:
                found.append(f"{path} ({text.count(DASH)})")
        self.assertEqual(found, [])

    def test_no_template_writes_one(self):
        entity = re.compile(r"&mdash;|&#8212;|&#x2014;", re.IGNORECASE)
        found = [path for path in tracked("*.html", "*.txt")
                 if "/templates/" in f"/{path}" and (DASH in (ROOT / path).read_text(encoding="utf-8")
                                                     or entity.search((ROOT / path).read_text(encoding="utf-8")))]
        self.assertEqual(found, [])
