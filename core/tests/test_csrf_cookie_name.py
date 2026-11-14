"""
The CSRF cookie the scripts fall back to is the one Django sets.

static/js/core/csrf.js reads the token from <meta name="csrf-token"> and,
on a page without it, from the cookie. Rasova names that cookie
settings.CSRF_COOKIE_NAME ("csrftoken2"); the fallback read "csrftoken",
a cookie Rasova never sets, so a page relying on it would have sent an
empty token and been refused (found 14 Nov 2026).

Run: python manage.py test core.tests.test_csrf_cookie_name
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class CsrfCookieNameTest(SimpleTestCase):

    def test_the_scripts_read_the_cookie_django_sets(self):
        text = (Path(settings.BASE_DIR) / "static" / "js" / "core" / "csrf.js").read_text(encoding="utf-8")
        found = re.search(r'export const CSRF_COOKIE = "([^"]+)";', text)
        self.assertIsNotNone(found, "csrf.js names its cookie in CSRF_COOKIE")
        self.assertEqual(found.group(1), settings.CSRF_COOKIE_NAME)
