"""
The production server's own files: systemd/*.service and deploy.sh.

Found 4 Nov 2026 on rasova.net:
- Beat (the scheduler) was started with nohup by deploy.sh, so after a
  reboot no scheduled job ran until the next deploy. Meanwhile the repo's
  celery.service had -B (a second beat inside the worker) while the
  server's copy didn't: copying the repo's file would have made every
  scheduled job (the 7 am digest, the 6 am invoices) run twice. Now beat
  is its own service and the worker never starts one.
- Pango was missing, so WeasyPrint couldn't load and every PDF failed,
  while Rasova started normally. deploy.sh now stops before changing
  anything when WeasyPrint can't load.

These read the files as text; each test fails on the files before 4 Nov.

Run: python manage.py test core.tests.test_server_units
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOT = Path(settings.BASE_DIR)


def exec_start(unit_text):
    """The ExecStart command of a unit, its continuation lines joined."""
    match = re.search(r"^ExecStart=((?:.*\\\n)*.*)$", unit_text, re.MULTILINE)
    return " ".join(match.group(1).replace("\\\n", " ").split()) if match else ""


class OneBeatTest(SimpleTestCase):

    def test_the_worker_never_runs_a_beat_of_its_own(self):
        command = exec_start((ROOT / "systemd" / "celery.service").read_text(encoding="utf-8"))

        self.assertIn("celery -A core worker", command)
        self.assertNotRegex(command, r"(^|\s)(-B|--beat)(\s|$)")

    def test_beat_is_a_service_that_starts_on_boot_and_after_a_crash(self):
        unit = (ROOT / "systemd" / "celery-beat.service").read_text(encoding="utf-8")

        self.assertIn("celery -A core beat", exec_start(unit))
        self.assertRegex(unit, r"(?m)^Restart=always$")
        self.assertRegex(unit, r"(?m)^WantedBy=multi-user\.target$")

    def test_deploy_restarts_the_beat_service_and_stops_any_second_beat(self):
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn("systemctl restart celery-beat", deploy)
        self.assertIn("systemctl show -p MainPID --value celery-beat", deploy)
        # The worker's nohup fallback must not start a beat either.
        for line in deploy.splitlines():
            if "nohup celery -A core worker" in line:
                self.assertNotRegex(line, r"\s(-B|--beat)(\s|$)")

    def test_deploy_installs_the_beat_service_from_the_code_it_just_pulled(self):
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        pull = deploy.find("git reset --hard")
        install = deploy.find("sudo cp \"$APP_DIR/systemd/celery-beat.service\" /etc/systemd/system/")
        restart = deploy.find("systemctl restart celery-beat")

        self.assertNotEqual(install, -1, "deploy.sh no longer installs the beat service")
        # After the pull, or it would install the previous version of the file;
        # before the restart, or the first deploy would start beat the old way.
        self.assertLess(pull, install)
        self.assertLess(install, restart)
        self.assertIn("cmp -s", deploy[pull:install])
        self.assertIn("systemctl enable celery-beat", deploy[install:restart])

    def test_deploy_reports_how_many_beats_run(self):
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")

        self.assertIn("pgrep -fc 'celery -A core beat'", deploy)
        self.assertIn('[ "$BEATS" = "1" ]', deploy)


class DeployChecksPdfsTest(SimpleTestCase):

    def test_deploy_stops_before_pulling_when_weasyprint_cannot_load(self):
        deploy = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        check = deploy.find('python -c "import weasyprint"')
        pull = deploy.find("git reset --hard")

        self.assertNotEqual(check, -1, "deploy.sh no longer checks that WeasyPrint loads")
        self.assertLess(check, pull, "the WeasyPrint check must run before the new code is pulled")
        self.assertIn("exit 1", deploy[check:pull])

    def test_the_runbook_installs_pango(self):
        runbook = (ROOT / "docs" / "DEPLOY.md").read_text(encoding="utf-8")

        for package in ("libpango-1.0-0", "libpangoft2-1.0-0", "fonts-dejavu-core"):
            self.assertIn(package, runbook)
