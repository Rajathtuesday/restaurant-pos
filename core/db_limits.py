"""Limits that keep one slow query from holding the whole server.

Production is one small server: two gunicorn workers of four threads, two
Celery processes and Postgres on 1 GB. One query that runs for minutes holds
a thread, its locks and its share of the memory, and the next requests queue
behind it. Three limits:

1. A statement timeout for web requests only. ``core/wsgi.py`` and
   ``core/asgi.py`` set ``DB_STATEMENT_TIMEOUT_MS`` before Django reads its
   settings, so gunicorn's connections get it and ``manage.py migrate``,
   Celery and the nightly backup (which may run long, on purpose) do not.
   Postgres cancels a statement past the limit; the request fails with an
   error that reaches the log and Sentry instead of hanging.
2. A slow query log: ``SlowQueryMiddleware`` writes every web query slower
   than ``SLOW_QUERY_MS`` to the ``pos.slow_sql`` logger (errors.log), with
   the address that ran it, so a page getting slower shows up before it
   reaches the timeout.
3. A connection budget: ``connection_budget()`` adds up what gunicorn, Celery
   and the jobs beside them can open, from the systemd units in the repo, and
   ``core/tests/test_db_limits.py`` holds it below what Postgres allows. The
   health check reports the live count.
"""
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger("pos.slow_sql")

WEB_STATEMENT_TIMEOUT_MS = 15_000
SLOW_QUERY_MS = 500
SQL_IN_LOG = 300            # characters of the statement kept in the log line

# Postgres defaults (postgresql.conf): connections a non-superuser may open.
PG_MAX_CONNECTIONS = 100
PG_SUPERUSER_RESERVED = 3
CONNECTIONS_WARN_SHARE = 0.80

UNITS = Path(__file__).resolve().parent.parent / "systemd"


def database_options(engine, environ):
    """OPTIONS for DATABASES['default'].

    A statement timeout when the process asked for one (web processes do, in
    wsgi.py) and the database is Postgres. Anything else gets no options.
    """
    if "postgresql" not in str(engine):
        return {}
    raw = (environ.get("DB_STATEMENT_TIMEOUT_MS") or "").strip()
    if not raw:
        return {}
    ms = int(raw)
    if ms <= 0:
        return {}
    return {"options": f"-c statement_timeout={ms}"}


def ask_for_web_limits(environ):
    """Called by wsgi.py and asgi.py before Django loads its settings."""
    environ.setdefault("DB_STATEMENT_TIMEOUT_MS", str(WEB_STATEMENT_TIMEOUT_MS))


class SlowQueryLog:
    """A database execute wrapper that logs statements slower than the limit."""

    def __init__(self, where, threshold_ms=SLOW_QUERY_MS, clock=time.monotonic):
        self.where = where
        self.threshold_ms = threshold_ms
        self.clock = clock

    def __call__(self, execute, sql, params, many, context):
        started = self.clock()
        try:
            return execute(sql, params, many, context)
        finally:
            took_ms = (self.clock() - started) * 1000
            if took_ms >= self.threshold_ms:
                text = " ".join(str(sql).split())
                if len(text) > SQL_IN_LOG:
                    text = text[:SQL_IN_LOG] + "..."
                logger.warning("slow query %d ms on %s: %s", took_ms, self.where, text)


class SlowQueryMiddleware:
    """Wraps every request's queries in a SlowQueryLog."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.db import connection
        log = SlowQueryLog(f"{request.method} {request.path}", threshold_ms=SLOW_QUERY_MS)
        with connection.execute_wrapper(log):
            return self.get_response(request)


def _flag(text, name, default):
    match = re.search(rf"--{name}[= ](\d+)", text)
    return int(match.group(1)) if match else default


def connection_budget(units=UNITS):
    """The most connections the server's processes can hold at one moment.

    Each gunicorn thread keeps its own connection (CONN_MAX_AGE), and a
    reload (``systemctl reload gunicorn``, a HUP) starts the new workers
    before the old ones leave, so gunicorn counts twice. Each Celery child
    holds one and the parent one more. Then beat, a migration during deploy,
    the nightly pg_dump and two for a person at psql or the health check.
    """
    gunicorn = (units / "gunicorn.service").read_text(encoding="utf-8")
    celery = (units / "celery.service").read_text(encoding="utf-8")
    parts = {
        "gunicorn": 2 * _flag(gunicorn, "workers", 1) * _flag(gunicorn, "threads", 1),
        "celery": _flag(celery, "concurrency", 1) + 1,
        "beat": 1,
        "migrate": 1,
        "backup": 1,
        "people": 2,
    }
    return sum(parts.values()), parts


def postgres_allows(max_connections=PG_MAX_CONNECTIONS, reserved=PG_SUPERUSER_RESERVED):
    return max_connections - reserved


def connections_in_use(conn):
    """(open, allowed) on a Postgres connection, (None, None) elsewhere."""
    if conn.vendor != "postgresql":
        return None, None
    with conn.cursor() as cur:
        cur.execute("SELECT pg_stat_clear_snapshot()")
        cur.execute("SHOW max_connections")
        max_connections = int(cur.fetchone()[0])
        cur.execute("SHOW superuser_reserved_connections")
        reserved = int(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend'")
        used = int(cur.fetchone()[0])
    return used, postgres_allows(max_connections, reserved)
