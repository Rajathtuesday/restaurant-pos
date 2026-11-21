"""
Celery can reach Redis on a fresh install (21 Nov 2026).

requirements.txt pinned celery but not kombu, its connection library, and
celery only asks for kombu>=5.6.0. A fresh install on 21 Nov got kombu
5.7.0, which no longer lists packaging among its requirements while its
Redis transport still imports it: every .delay() (printing, digests, the
nightly jobs) failed with "No module named 'packaging'". A machine that
already had packaging from another library (this laptop) never saw it;
a clean Linux container did.
Run: python manage.py test core.tests.test_dependencies
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


def pins():
    text = (Path(settings.BASE_DIR) / "requirements.txt").read_text(encoding="utf-8")
    return dict(re.findall(r"(?m)^([A-Za-z0-9_.-]+)==(\S+)", text))


class CeleryReachesRedisTest(SimpleTestCase):

    def test_the_redis_transport_imports(self):
        # What celery imports to send a task to Redis.
        import kombu.transport.redis  # noqa: F401

    def test_kombu_and_packaging_are_pinned(self):
        found = pins()
        self.assertIn("kombu", found, "pin kombu next to celery in requirements.txt")
        self.assertIn("packaging", found, "kombu's Redis transport imports packaging; pin it")

    def test_the_installed_kombu_is_the_pinned_one(self):
        from importlib.metadata import version
        self.assertEqual(version("kombu"), pins()["kombu"])
