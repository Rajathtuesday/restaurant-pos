"""
CI runs the browser tests (T2, 13 Nov 2026).

Why: the browser tests (core/tests/browser.py: the smoke walk, every page
moved to modules, offline, printing, empty states) skip where Playwright or
Chromium is missing, and CI had neither, so all of them were skipped exactly
where they matter most. requirements-test.txt now pins Playwright and CI
installs Chromium before the suite.

Run: python manage.py test core.tests.test_ci_runs_browser_tests
"""
import importlib.metadata
import re

from django.conf import settings
from django.test import SimpleTestCase


class CiBrowserTestsTest(SimpleTestCase):

    def test_ci_installs_chromium_before_the_suite(self):
        ci = (settings.BASE_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        install = ci.find("python -m playwright install --with-deps chromium")
        suite = ci.find("- name: Run test suite")
        self.assertGreater(install, 0, "CI does not install Chromium")
        self.assertLess(install, suite, "Chromium must be installed before the suite runs")

    def test_the_pinned_playwright_is_the_one_on_this_machine(self):
        pinned = re.search(r"^playwright==([\d.]+)$",
                           (settings.BASE_DIR / "requirements-test.txt").read_text(encoding="utf-8"), re.M)
        self.assertIsNotNone(pinned, "requirements-test.txt does not pin Playwright")
        self.assertEqual(pinned.group(1), importlib.metadata.version("playwright"))
