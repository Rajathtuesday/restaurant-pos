"""
docs/RUNBOOK.md names files, logs, services, flags and numbers. A runbook
read at 9 pm on a Saturday must not send anyone to a script that moved or a
log that is now called something else. This holds every name in it to the
code.

Run: python manage.py test core.tests.test_runbook
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core import db_limits, health

ROOT = Path(settings.BASE_DIR)
RUNBOOK = ROOT / "docs" / "RUNBOOK.md"
OS_SERVICES = {"nginx", "postgresql", "redis-server"}


def text():
    return RUNBOOK.read_text(encoding="utf-8")


class RunbookTest(SimpleTestCase):

    def test_every_repo_file_it_names_exists(self):
        named = set(re.findall(r"\b((?:scripts|docs)/[\w./-]+\.(?:py|sh|md))", text())) | {"deploy.sh"}
        self.assertIn("scripts/backup/restore_drill.py", named)
        for path in sorted(named):
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), f"{path} is named in the runbook")

    def test_every_log_it_names_is_written_by_something(self):
        writers = "".join(p.read_text(encoding="utf-8") for p in [
            ROOT / "core" / "settings.py", *sorted((ROOT / "systemd").glob("*.service"))])
        logs = set(re.findall(r"logs/([\w.-]+\.log)\b", text()))
        self.assertTrue({"gunicorn_error.log", "errors.log", "celery.log"} <= logs, logs)
        for name in sorted(logs):
            with self.subTest(log=name):
                self.assertIn(name, writers)

    def test_every_service_it_restarts_exists(self):
        ours = {p.stem for p in (ROOT / "systemd").glob("*.service")}
        named = set()
        for line in re.findall(r"systemctl (?:status|restart) ([\w -]+?)(?: --no-pager|`|$)", text(), re.M):
            named |= set(line.split())
        named.discard("<it>")
        self.assertTrue({"gunicorn", "celery"} <= named, named)
        for service in sorted(named):
            with self.subTest(service=service):
                self.assertIn(service, ours | OS_SERVICES)

    def test_the_flags_it_uses_exist(self):
        self.assertIn("deploy.sh --undo", text())
        self.assertIn("--undo", (ROOT / "deploy.sh").read_text(encoding="utf-8"))
        self.assertIn("restore_drill.py --keep", text())
        self.assertIn('"--keep"', (ROOT / "scripts" / "backup" / "restore_drill.py").read_text(encoding="utf-8"))
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("versions/last/before.sql.gz", text())
        self.assertTrue("STATE=$APP_DIR/versions/last" in deploy and '"$STATE/before.sql.gz"' in deploy,
                        "deploy.sh no longer keeps versions/last/before.sql.gz")

    def test_the_health_checks_and_numbers_it_quotes_are_the_codes(self):
        checks = set(re.findall(r'_check\("(\w+)"', Path(health.__file__).read_text(encoding="utf-8")))
        quoted = re.findall(r"`(\w+): false`", text())
        self.assertTrue({"disk", "swap", "connections"} <= set(quoted), quoted)
        for name in quoted:
            with self.subTest(check=name):
                self.assertIn(name, checks)
        self.assertIn(f"after {db_limits.WEB_STATEMENT_TIMEOUT_MS // 1000} seconds", text())
        self.assertIn('grep "slow query"', text())
        self.assertIn('"slow query %d ms on %s: %s"', Path(db_limits.__file__).read_text(encoding="utf-8"))

    def test_no_long_dashes(self):
        self.assertNotIn("\u2014", text())
