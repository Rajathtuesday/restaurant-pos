"""
Limits on the database (B13): one slow query must not hold the server.

Why: production is one 1 GB server. A web query that runs for minutes holds a
gunicorn thread, its locks and memory, and every request after it queues. The
limits are in core/db_limits.py: a statement timeout for web processes only,
a log line for every query slower than half a second, and a connection budget
the systemd units must stay inside.

Run: python manage.py test core.tests.test_db_limits
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import connection, connections
from django.db.utils import OperationalError
from django.test import Client, SimpleTestCase, TestCase

from core import db_limits, health
from core.db_limits import SlowQueryLog, connection_budget, database_options, postgres_allows

PG = "django.db.backends.postgresql"


class StatementTimeoutOptionsTest(SimpleTestCase):

    def test_postgres_web_process_gets_a_statement_timeout(self):
        self.assertEqual(database_options(PG, {"DB_STATEMENT_TIMEOUT_MS": "15000"}),
                         {"options": "-c statement_timeout=15000"})

    def test_a_process_that_did_not_ask_gets_none(self):
        self.assertEqual(database_options(PG, {}), {}, "migrate and Celery may run long")
        self.assertEqual(database_options(PG, {"DB_STATEMENT_TIMEOUT_MS": "0"}), {})

    def test_sqlite_gets_no_postgres_options(self):
        self.assertEqual(database_options("django.db.backends.sqlite3",
                                          {"DB_STATEMENT_TIMEOUT_MS": "15000"}), {})

    def test_wsgi_asks_for_the_web_limit_and_keeps_one_set_by_hand(self):
        env = {}
        db_limits.ask_for_web_limits(env)
        self.assertEqual(env["DB_STATEMENT_TIMEOUT_MS"], str(db_limits.WEB_STATEMENT_TIMEOUT_MS))
        env = {"DB_STATEMENT_TIMEOUT_MS": "4000"}
        db_limits.ask_for_web_limits(env)
        self.assertEqual(env["DB_STATEMENT_TIMEOUT_MS"], "4000")


class WhoGetsTheTimeoutTest(SimpleTestCase):
    """Gunicorn loads core.wsgi; manage.py and Celery load the settings only."""

    def options_after(self, code):
        env = {k: v for k, v in os.environ.items() if k != "DB_STATEMENT_TIMEOUT_MS"}
        env.update(DJANGO_SETTINGS_MODULE="core.settings", DB_ENGINE=PG)
        out = subprocess.run(
            [sys.executable, "-c", code + "; from django.conf import settings; "
             "print(settings.DATABASES['default'].get('OPTIONS'))"],
            cwd=settings.BASE_DIR, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return out.stdout.strip().splitlines()[-1]

    def test_a_web_process_has_the_timeout(self):
        self.assertIn("statement_timeout=15000", self.options_after("import core.wsgi"))

    def test_a_management_or_celery_process_does_not(self):
        self.assertEqual(self.options_after("import django; django.setup()"), "{}")


class StatementTimeoutOnPostgresTest(TestCase):
    """The option string really reaches Postgres and cancels a long statement."""

    def setUp(self):
        if connection.vendor != "postgresql":
            self.skipTest("Postgres only")

    def test_a_statement_past_the_limit_is_cancelled(self):
        params = dict(connection.settings_dict)
        params["OPTIONS"] = database_options(PG, {"DB_STATEMENT_TIMEOUT_MS": "200"})
        probe = connections["default"].__class__(params, alias="timeout_probe")
        try:
            with probe.cursor() as cur:
                cur.execute("SELECT 1")
                self.assertEqual(cur.fetchone(), (1,))
                with self.assertRaisesMessage(OperationalError, "statement timeout"):
                    cur.execute("SELECT pg_sleep(2)")
        finally:
            probe.close()


class FakeClock:
    def __init__(self, *readings):
        self.readings = list(readings)

    def __call__(self):
        return self.readings.pop(0)


def run(log, sql="SELECT 1", fails=False):
    def execute(sql, params, many, context):
        if fails:
            raise OperationalError("canceling statement due to statement timeout")
        return "rows"
    return log(execute, sql, None, False, {})


class SlowQueryLogTest(SimpleTestCase):

    def test_a_slow_query_is_logged_with_its_address(self):
        log = SlowQueryLog("GET /reports/sales/", threshold_ms=500, clock=FakeClock(10.0, 10.8))
        with self.assertLogs("pos.slow_sql", "WARNING") as logs:
            self.assertEqual(run(log, "SELECT *\n   FROM orders_order"), "rows")
        self.assertEqual(logs.output, [
            "WARNING:pos.slow_sql:slow query 800 ms on GET /reports/sales/: SELECT * FROM orders_order"])

    def test_a_quick_query_is_not(self):
        log = SlowQueryLog("GET /", threshold_ms=500, clock=FakeClock(10.0, 10.1))
        with self.assertNoLogs("pos.slow_sql"):
            run(log)

    def test_a_long_statement_is_cut(self):
        log = SlowQueryLog("GET /", threshold_ms=0, clock=FakeClock(0, 1))
        with self.assertLogs("pos.slow_sql") as logs:
            run(log, "SELECT " + "x, " * 400)
        self.assertLess(len(logs.output[0]), db_limits.SQL_IN_LOG + 80)
        self.assertTrue(logs.output[0].endswith("..."))

    def test_a_cancelled_statement_is_logged_and_still_fails(self):
        log = SlowQueryLog("POST /orders/", threshold_ms=500, clock=FakeClock(0, 15.0))
        with self.assertLogs("pos.slow_sql") as logs, self.assertRaises(OperationalError):
            run(log, fails=True)
        self.assertIn("15000 ms on POST /orders/", logs.output[0])


class SlowQueryMiddlewareTest(TestCase):

    def test_every_request_runs_its_queries_through_the_log(self):
        from accounts.models import User
        User.objects.create_user(username="slowlog", password="pw-12345678", is_superuser=True)
        client = Client()
        client.login(username="slowlog", password="pw-12345678")
        with mock.patch.object(db_limits, "SLOW_QUERY_MS", 0), \
                self.assertLogs("pos.slow_sql", "WARNING") as logs:
            client.get("/health/")
        self.assertTrue(any("on GET /health/" in line for line in logs.output), logs.output)


class ConnectionBudgetTest(SimpleTestCase):

    def test_the_server_units_fit_inside_what_postgres_allows(self):
        total, parts = connection_budget()
        self.assertEqual(parts["gunicorn"], 16, "2 workers x 4 threads, twice during a reload")
        self.assertEqual(parts["celery"], 3, "2 children and the parent")
        self.assertLess(total, postgres_allows() * db_limits.CONNECTIONS_WARN_SHARE,
                        f"{total} connections possible: {parts}")

    def test_raising_the_workers_past_the_limit_is_caught(self):
        units = Path(tempfile.mkdtemp())
        (units / "gunicorn.service").write_text("gunicorn --workers 6 --threads 8 core.wsgi")
        (units / "celery.service").write_text("celery -A core worker --concurrency=4")
        total, _parts = connection_budget(units)
        self.assertGreater(total, postgres_allows())


class ConnectionsHealthTest(TestCase):

    def test_postgres_reports_how_many_are_open(self):
        if connection.vendor != "postgresql":
            self.skipTest("Postgres only")
        ok, detail = health.check_connections()
        self.assertTrue(ok, detail)
        self.assertRegex(detail, r"^\d+ of \d+ database connections open \(\d+%\)$")

    def test_too_many_open_is_a_failure(self):
        if connection.vendor != "postgresql":
            self.skipTest("Postgres only")
        ok, _detail = health.check_connections(warn_share=0.000001)
        self.assertFalse(ok)

    def test_another_database_does_not_apply(self):
        self.assertEqual(health.check_connections(conn=mock.Mock(vendor="sqlite")),
                         (None, "not Postgres: no connection limit"))
