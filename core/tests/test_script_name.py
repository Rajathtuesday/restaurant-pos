"""
Rasova always runs at the root of its own address (rasova.net/, a box's
http://rasova.local/), never under a sub-path.

Django puts a request's SCRIPT_NAME in front of every address it builds:
request.path, reverse(), and the redirect that adds a missing slash. Without
FORCE_SCRIPT_NAME, SCRIPT_NAME comes from the request, and to a browser an
address starting "//evil.example" is another website, so a request could
make Rasova send the browser off-site. settings.FORCE_SCRIPT_NAME = ""
makes Django ignore it everywhere.

Found 4 Nov 2026 from code scanning alert #114 (covers/views.py built a
redirect from request.path). Every test here fails without the setting.

Run: python manage.py test core.tests.test_script_name
"""
from django.conf import settings
from django.test import Client, TestCase

ODD = "//evil.example"


class ScriptNameFromOutsideTest(TestCase):

    def test_rasova_runs_at_the_root_of_its_address(self):
        self.assertEqual(settings.FORCE_SCRIPT_NAME, "")

    def test_the_missing_slash_redirect_stays_on_rasova(self):
        resp = Client().get("/login", SCRIPT_NAME=ODD)

        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp["Location"], "/login/")

    def test_a_page_that_needs_a_login_sends_back_here_after_it(self):
        resp = Client().get("/dashboard/", SCRIPT_NAME=ODD)

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/login/?next=/dashboard/")
