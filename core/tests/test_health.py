"""
/health/ for whoever may see the detail: the things that take a small
server down before its database does.

Why: /health/ said "healthy" as long as the database answered. A stopped
Celery worker (no daily report, no demo reset), a disk at 99%, swap full,
a backup that quietly stopped running, or a print agent that died at
7 pm all looked healthy. The detail (core/health.py) checks each, and one
failure makes the answer 503, so an outside monitor alerts on the code.

Run: python manage.py test core.tests.test_health
"""
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from django_celery_results.models import TaskResult

from accounts.models import User
from core import health
from core.tasks import heartbeat
from printing.models import PrintJob
from tenants.models import Outlet, Tenant

TOKEN = "monitor-secret-0123456789"
PLENTY = (100 * 2**30, 40 * 2**30, 60 * 2**30)       # total, used, free: 60% free
NEARLY_FULL = (100 * 2**30, 97 * 2**30, 3 * 2**30)

MEMINFO = "MemTotal: 1000000 kB\nSwapTotal:      2097148 kB\nSwapFree:        209714 kB\n"


@override_settings(HEALTH_TOKEN=TOKEN)
class HealthDetailTest(TestCase):

    def setUp(self):
        self.now = timezone.now()
        self.tmp = Path(tempfile.mkdtemp())
        self.marker = self.tmp / "last_backup_ok"
        health.record_backup(self.marker, now=self.now - timedelta(hours=3))
        TaskResult.objects.create(task_id="beat-1", task_name=health.HEARTBEAT_TASK, status="SUCCESS",
                                  date_done=self.now - timedelta(seconds=40))
        patches = [
            mock.patch("core.health.backup_marker", return_value=self.marker),
            mock.patch("core.health.shutil.disk_usage", return_value=PLENTY),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        tenant = Tenant.objects.create(name="Health Cafe")
        self.outlet = Outlet.objects.create(tenant=tenant, name="Main")
        self.tenant = tenant

    def ask(self, client=None, **headers):
        return (client or Client()).get("/health/", **headers)

    def detail(self):
        response = self.ask(HTTP_X_HEALTH_TOKEN=TOKEN)
        return response.status_code, response.json()

    def job(self, minutes_ago, status=PrintJob.PENDING):
        job = PrintJob.objects.create(tenant=self.tenant, outlet=self.outlet, payload={"text": "x"}, status=status)
        PrintJob.objects.filter(pk=job.pk).update(created_at=self.now - timedelta(minutes=minutes_ago))

    # ---- who sees what -------------------------------------------------------

    def test_everyone_else_gets_the_one_line_answer(self):
        for headers in ({}, {"HTTP_X_HEALTH_TOKEN": "wrong"}, {"HTTP_X_HEALTH_TOKEN": ""}):
            response = self.ask(**headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "healthy", "database": "connected"})

    def test_an_owner_is_not_a_superuser(self):
        owner = User.objects.create_user(username="h-owner", password="pw", role="owner",
                                         tenant=self.tenant, outlet=self.outlet)
        client = Client()
        client.force_login(owner)
        self.assertNotIn("checks", self.ask(client).json())

    def test_a_superuser_sees_the_detail(self):
        boss = User.objects.create_superuser(username="h-boss", password="pw")
        client = Client()
        client.force_login(boss)
        self.assertIn("checks", self.ask(client).json())

    @override_settings(HEALTH_TOKEN="")
    def test_no_token_configured_means_no_token_works(self):
        self.assertNotIn("checks", self.ask(HTTP_X_HEALTH_TOKEN="").json())

    # ---- the checks ----------------------------------------------------------

    def test_a_healthy_server_says_so_check_by_check(self):
        status, body = self.detail()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["status"], "healthy")
        self.assertEqual(set(body["checks"]), {"database", "cache", "heartbeat", "disk", "swap", "backup", "printing",
                                                "connections"})
        self.assertTrue(body["checks"]["database"]["ok"])
        self.assertTrue(body["checks"]["heartbeat"]["ok"])
        self.assertTrue(body["checks"]["backup"]["ok"])
        self.assertEqual(body["checks"]["printing"]["detail"], "nothing waiting to print")
        self.assertIsNone(body["checks"]["cache"]["ok"], "the test run has no Redis")

    def test_a_stopped_worker_or_beat_is_a_failure(self):
        TaskResult.objects.update(date_done=self.now - timedelta(minutes=12))
        status, body = self.detail()
        self.assertEqual(status, 503)
        self.assertFalse(body["checks"]["heartbeat"]["ok"])
        self.assertIn("12 min ago", body["checks"]["heartbeat"]["detail"])

    def test_a_heartbeat_never_seen_is_a_failure(self):
        TaskResult.objects.all().delete()
        status, body = self.detail()
        self.assertEqual(status, 503)
        self.assertIn("no heartbeat ever recorded", body["checks"]["heartbeat"]["detail"])

    def test_a_backup_more_than_a_day_old_or_missing_is_a_failure(self):
        health.record_backup(self.marker, now=self.now - timedelta(hours=30))
        self.assertFalse(self.detail()[1]["checks"]["backup"]["ok"])
        self.marker.unlink()
        status, body = self.detail()
        self.assertEqual((status, body["checks"]["backup"]["detail"]), (503, "no good backup recorded yet"))

    def test_a_disk_nearly_full_is_a_failure(self):
        with mock.patch("core.health.shutil.disk_usage", return_value=NEARLY_FULL):
            status, body = self.detail()
        self.assertEqual(status, 503)
        self.assertEqual(body["checks"]["disk"], {"ok": False, "detail": "3 GB free of 100 GB (3%)"})

    def test_swap_nearly_full_is_a_failure(self):
        meminfo = self.tmp / "meminfo"
        meminfo.write_text(MEMINFO)
        self.assertEqual(health.swap_from_meminfo(MEMINFO), (2097148, 209714))
        self.assertEqual(health.check_swap(meminfo), (False, "90% of 2047 MB swap in use"))
        meminfo.write_text("SwapTotal: 0 kB\nSwapFree: 0 kB\n")
        self.assertEqual(health.check_swap(meminfo), (None, "no swap configured"))
        self.assertEqual(health.check_swap(self.tmp / "absent")[0], None)

    def test_a_print_job_stuck_for_ten_minutes_is_a_failure(self):
        self.job(2)
        self.assertTrue(self.detail()[1]["checks"]["printing"]["ok"], "two minutes is a busy kitchen, not a fault")
        self.job(25, status=PrintJob.PROCESSING)
        self.job(90, status=PrintJob.DONE)                 # finished long ago: not waiting
        status, body = self.detail()
        self.assertEqual(status, 503)
        self.assertEqual(body["checks"]["printing"]["detail"],
                         "2 job(s) waiting, the oldest for 25 min: is a print agent down?")

    def test_a_check_that_breaks_fails_without_showing_why(self):
        with mock.patch("core.health.check_disk", side_effect=OSError("/secret/path password=hunter2")):
            response = self.ask(HTTP_X_HEALTH_TOKEN=TOKEN)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("hunter2", response.content.decode())
        self.assertNotIn("/secret/path", response.content.decode())

    def test_the_detail_names_no_restaurant(self):
        self.job(25)
        self.assertNotIn("Health Cafe", self.ask(HTTP_X_HEALTH_TOKEN=TOKEN).content.decode())


class HeartbeatTest(TestCase):

    def test_beat_sends_the_heartbeat_every_minute(self):
        from django.conf import settings
        entry = settings.CELERY_BEAT_SCHEDULE["heartbeat"]
        self.assertEqual((entry["task"], entry["schedule"]), (health.HEARTBEAT_TASK, 60.0))
        self.assertEqual(heartbeat.name, health.HEARTBEAT_TASK)

    def test_the_heartbeat_returns_its_time(self):
        self.assertTrue(heartbeat())

    def test_the_backup_script_records_a_good_backup_after_the_upload(self):
        source = (Path(__file__).resolve().parents[2] / "scripts" / "backup" / "backup_to_r2.py").read_text(encoding="utf-8")
        self.assertLess(source.index("uploaded s3://"), source.index("record_backup()"),
                        "the marker must be written only after the upload is confirmed")
