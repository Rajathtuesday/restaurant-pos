"""
A module's code is never served stale beside a newer module (15 Nov 2026).

Why: on a development machine (python manage.py runserver) Django's own
static handler served /static/ with no Cache-Control, so the browser guessed
how long each file stayed fresh. After a change, a page ran the new
core/app.js with an old core/page_actions.js ("does not provide an export
named 'pageSubmitActions'"), the module graph failed to load, and every
button on the page did nothing: a menu item could not be deleted.

Two guards: runserver serves /static/ through WhiteNoise (max-age=0 under
DEBUG), and the service worker asks the server every time for a static
file whose name carries no hash (a 304 when nothing changed).
Run: python manage.py test core.tests.test_dev_static_cache
"""
import re
from pathlib import Path

from django.conf import settings
from django.core.management import get_commands
from django.test import SimpleTestCase


class DevStaticCacheTest(SimpleTestCase):

    def test_runserver_serves_static_through_whitenoise(self):
        self.assertEqual(get_commands()["runserver"], "whitenoise.runserver_nostatic")
        apps = settings.INSTALLED_APPS
        self.assertLess(apps.index("whitenoise.runserver_nostatic"), apps.index("django.contrib.staticfiles"))

    def test_the_service_worker_revalidates_unhashed_static_files(self):
        sw = (Path(settings.BASE_DIR) / "static" / "sw.js").read_text(encoding="utf-8")
        branch = re.search(r"!HASHED_NAME\.test\(url\.pathname\)\)\s*\{(.*?)return;", sw, re.S)
        self.assertIsNotNone(branch, "the unhashed static branch moved; update this test")
        self.assertRegex(branch.group(1), r"fetch\(request,\s*\{\s*cache:\s*'no-cache'\s*\}\)")
