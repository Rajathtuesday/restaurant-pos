"""
No test reaches an outside service with a real key (T3, 13 Nov 2026).

Why: core/settings.py loads .env, and the developer laptop's .env holds the
production keys. A test reached Google's AI with the real key (a real 503
"high demand" in the test log), and every test run had to blank the R2
storage keys by hand, or risk writing to the production bucket. Under the
test runner the keys are now blanked before settings read them.

Run: python manage.py test core.tests.test_no_real_services
"""
import os

from django.conf import settings
from django.test import SimpleTestCase

SECRETS = ("AWS_STORAGE_BUCKET_NAME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_S3_ENDPOINT_URL",
           "GOOGLE_API_KEY", "GOOGLE_API_KEY_FALLBACK", "ANTHROPIC_API_KEY", "RASOVA_RAZORPAY_KEY_ID", "RASOVA_RAZORPAY_KEY_SECRET",
           "EMAIL_PASSWORD", "SENTRY_DSN")


class NoRealServicesTest(SimpleTestCase):

    def test_every_secret_is_blank_under_the_test_runner(self):
        self.assertEqual({k: os.environ.get(k, "") for k in SECRETS}, {k: "" for k in SECRETS})

    def test_files_are_stored_on_disk_not_in_the_bucket(self):
        self.assertNotIn("S3", settings.STORAGES["default"]["BACKEND"])
        self.assertFalse(getattr(settings, "AWS_ACCESS_KEY_ID", ""))

    def test_the_menu_ai_is_off(self):
        from core.ai_service import AIService
        self.assertIsNone(AIService().client)

    def test_payments_and_mail_have_no_real_keys(self):
        self.assertEqual(settings.RASOVA_RAZORPAY_KEY_SECRET, "")
        self.assertEqual(settings.EMAIL_HOST_PASSWORD, "")
