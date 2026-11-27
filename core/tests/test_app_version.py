"""
Asking an old Android app to update (B16, 13 Nov 2026).

Why: an old Rasova app stops printing, and the restaurant only sees "Rasova
does not print". The app now names its real version in every request
(rasova_android AppUserAgent, tested in AppUserAgentTest.kt); below
core.app_version.MINIMUM_ANDROID_APP every page shows a banner saying so.
Before 1.2.1 every app said "1.0" whatever it was.

Since 27 Nov 2026 rasova.net does not serve the app (work on it is paused):
in the cloud the banner points to the phone's browser, and only a box,
which serves its own copy, links to a download.

Run: python manage.py test core.tests.test_app_version
"""
from unittest import mock

from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from core.app_version import MINIMUM_ANDROID_APP, app_update, app_version, is_older
from core.tests.test_tenant_walk import Restaurant

WEBVIEW = "Mozilla/5.0 (Linux; Android 14; Pixel 7; wv) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"
OLD_APP = WEBVIEW + " RasovaPOS-Android/1.0"          # every app before 1.2.1
NEW_APP = WEBVIEW + " RasovaPOS-Android/1.2.1"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36"


class VersionTest(SimpleTestCase):

    def test_the_apps_version_from_its_user_agent(self):
        self.assertEqual(app_version(NEW_APP), "1.2.1")
        self.assertEqual(app_version(OLD_APP), "1.0")
        self.assertIsNone(app_version(BROWSER))
        self.assertIsNone(app_version(""))
        self.assertIsNone(app_version(None))

    def test_versions_compare_as_numbers(self):
        self.assertTrue(is_older("1.0", "1.2.1"))
        self.assertTrue(is_older("1.9", "1.10"))
        self.assertFalse(is_older("1.10", "1.9"))
        self.assertFalse(is_older("1.2", "1.2.0"))
        self.assertFalse(is_older("1.2.1", "1.2.1"))
        self.assertFalse(is_older("1.3.0-beta", "1.2.1"))

    def test_today_the_minimum_asks_no_one(self):
        """1.2.1 is not the download yet: raising the minimum now would ask
        every phone to install an app that is not there."""
        self.assertEqual(MINIMUM_ANDROID_APP, "1.0")
        request = RequestFactory().get("/", HTTP_USER_AGENT=OLD_APP)
        self.assertIsNone(app_update(request))

    def test_below_the_minimum_the_banner_has_what_it_needs(self):
        factory = RequestFactory()
        # rasova.net has no app to offer; a box serves its own
        self.assertEqual(app_update(factory.get("/", HTTP_USER_AGENT=OLD_APP), minimum="1.2.1"),
                         {"version": "1.0", "minimum": "1.2.1", "download": None})
        with override_settings(BOX=True):
            self.assertEqual(app_update(factory.get("/", HTTP_USER_AGENT=OLD_APP), minimum="1.2.1")["download"],
                             "/android/rasova.apk")
        self.assertIsNone(app_update(factory.get("/", HTTP_USER_AGENT=NEW_APP), minimum="1.2.1"))
        self.assertIsNone(app_update(factory.get("/", HTTP_USER_AGENT=BROWSER), minimum="1.2.1"))


@mock.patch("core.app_version.MINIMUM_ANDROID_APP", "1.2.1")
class BannerTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.r = Restaurant("Alpha", "appver-alpha")

    def page(self, user_agent, url="/billing/"):
        client = Client(HTTP_USER_AGENT=user_agent)
        client.force_login(self.r.owner)
        return client.get(url).content.decode()

    def test_an_old_app_sees_the_banner_on_every_page(self):
        for url in ("/billing/", "/dashboard/", "/kitchen/"):
            html = self.page(OLD_APP, url)
            self.assertIn("data-app-update", html, url)
            self.assertIn("This Rasova app is out of date", html)
            # no link to a download rasova.net does not have
            self.assertNotIn("rasova.apk", html)
            self.assertIn("open Rasova in the phone's browser", html)

    def test_a_new_app_and_a_browser_do_not(self):
        self.assertNotIn("data-app-update", self.page(NEW_APP))
        self.assertNotIn("data-app-update", self.page(BROWSER))

    def test_rasova_net_no_longer_serves_the_app(self):
        """27 Nov 2026: the app download came off rasova.net (the Android
        app is paused). A box keeps its own copy (box/docker/android/)."""
        response = Client().get("/android/rasova.apk")
        self.assertEqual(response.status_code, 404)
