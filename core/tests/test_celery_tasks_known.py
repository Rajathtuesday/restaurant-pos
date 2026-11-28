"""
Every task beat sends is one the worker knows.

Why: the heartbeat (core.tasks.heartbeat, beat every minute since 13 Nov
2026) lives in core/tasks.py, and core is the project package, not an
installed app, so the worker's app.autodiscover_tasks() never imported it.
On production the worker threw every heartbeat away ("Received
unregistered task of type 'core.tasks.heartbeat'", about 1,440 errors a
day in celery.log), and /health/'s heartbeat check could never pass.
core/tests/test_health.py imports core.tasks itself, so it never saw it.

This asks a fresh process, loaded the way the worker loads (django.setup,
then the Celery app's default modules), which tasks it knows.

Run: python manage.py test core.tests.test_celery_tasks_known
"""
import json
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

PROBE = """
import json, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django
django.setup()
from core.celery import app
app.loader.import_default_modules()
from django.conf import settings
sent = {name: entry["task"] for name, entry in settings.CELERY_BEAT_SCHEDULE.items()}
print("PROBE " + json.dumps({"sent": sent, "missing": sorted(t for t in sent.values() if t not in app.tasks)}))
"""


class EveryScheduledTaskIsKnownToTheWorkerTest(SimpleTestCase):

    def test_a_fresh_worker_knows_every_task_beat_sends(self):
        result = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True,
                                cwd=str(settings.BASE_DIR), timeout=180)
        lines = [line for line in result.stdout.splitlines() if line.startswith("PROBE ")]
        self.assertTrue(lines, f"the probe printed nothing: {result.stderr[-2000:]}")
        found = json.loads(lines[-1][len("PROBE "):])
        self.assertIn("core.tasks.heartbeat", found["sent"].values())
        self.assertEqual(found["missing"], [], "beat sends these, the worker would throw them away")
