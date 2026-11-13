"""
What /health/ says when someone allowed to know asks for the detail.

The public answer (core.views.health_check) is one line for a load
balancer: can the app reach its database. A small server goes down for
other reasons first, each of which has taken one down for someone:

  cache        Redis, which holds the sessions and the rate limits
  heartbeat    the Celery worker and beat: beat queues core.tasks.heartbeat
               every minute, the worker runs it, and its result row is the
               proof both are alive (daily reports, demo resets and the
               print sweep all need them)
  disk         free space where the app, its logs and pg_wal live
  swap         a t3.micro with swap filling up is minutes from stalling
  backup       the age of the last good nightly backup (the backup script
               writes the marker only after its upload is confirmed)
  printing     the oldest print job no agent has finished: printing is
               what fails most in a real restaurant

Each check answers ok True (fine), False (act now) or None (does not
apply here, such as swap on a laptop). One False makes the answer 503,
so an outside monitor can alert on the status code alone.

Nothing here puts an exception's text in the answer: it can carry a host
or a password. The detail goes to the log.
"""
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger("pos.core")

HEARTBEAT_TASK = "core.tasks.heartbeat"
HEARTBEAT_STALE = timedelta(minutes=3)       # beat sends one a minute
DISK_FREE_MIN = 0.10                         # 10% of the disk
SWAP_USED_MAX = 0.80
BACKUP_STALE = timedelta(hours=26)           # nightly at 2 am, plus slack
PRINT_JOB_STUCK = timedelta(minutes=10)


def backup_marker():
    """Where the backup script records its last good run."""
    return Path(settings.BASE_DIR) / "logs" / "last_backup_ok"


def record_backup(marker=None, now=None):
    """Called by scripts/backup/backup_to_r2.py after the upload is
    confirmed: the marker's time is the last good backup."""
    path = marker or backup_marker()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((now or timezone.now()).isoformat(), encoding="utf-8")


def _check(name, fn):
    try:
        ok, detail = fn()
    except Exception:  # noqa: BLE001 -- a broken check is a failed check, logged, never shown
        logger.exception("Health check %s failed", name)
        ok, detail = False, "the check itself failed; see the server log"
    return name, {"ok": ok, "detail": detail}


def check_database():
    connection.ensure_connection()
    return True, "connected"


def check_cache():
    backend = settings.CACHES["default"]["BACKEND"]
    if "redis" not in backend.lower():
        return None, "no Redis configured (local memory cache)"
    from django.core.cache import cache
    cache.set("health:ping", "pong", 30)
    answered = cache.get("health:ping") == "pong"
    return answered, "Redis answered" if answered else "Redis did not answer"


def check_heartbeat(now):
    if "heartbeat" not in getattr(settings, "CELERY_BEAT_SCHEDULE", {}):
        return None, "no heartbeat scheduled here"
    from django_celery_results.models import TaskResult
    last = (TaskResult.objects.filter(task_name=HEARTBEAT_TASK, status="SUCCESS")
            .order_by("-date_done").values_list("date_done", flat=True).first())
    if last is None:
        return False, "no heartbeat ever recorded: is the Celery worker or beat running?"
    age = now - last
    minutes = int(age.total_seconds() // 60)
    if age > HEARTBEAT_STALE:
        return False, f"last heartbeat {minutes} min ago: the worker or beat has stopped"
    return True, f"last heartbeat {int(age.total_seconds())} s ago"


def check_disk():
    total, _used, free = shutil.disk_usage(settings.BASE_DIR)
    share = free / total if total else 0
    detail = f"{free // 2**30} GB free of {total // 2**30} GB ({share:.0%})"
    return share >= DISK_FREE_MIN, detail


def swap_from_meminfo(text):
    """(total kB, free kB) from /proc/meminfo's text."""
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("SwapTotal", "SwapFree"):
            values[key] = int(rest.split()[0])
    return values.get("SwapTotal", 0), values.get("SwapFree", 0)


def check_swap(meminfo=Path("/proc/meminfo")):
    if not meminfo.exists():
        return None, "not Linux: no swap figure"
    total, free = swap_from_meminfo(meminfo.read_text())
    if not total:
        return None, "no swap configured"
    used = (total - free) / total
    return used <= SWAP_USED_MAX, f"{used:.0%} of {total // 1024} MB swap in use"


def check_backup(now, marker=None):
    path = marker or backup_marker()
    if not path.exists():
        return False, "no good backup recorded yet"
    from datetime import datetime
    when = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    hours = (now - when).total_seconds() / 3600
    if now - when > BACKUP_STALE:
        return False, f"last good backup {hours:.0f} hours ago"
    return True, f"last good backup {hours:.1f} hours ago"


def check_printing(now):
    from printing.models import PrintJob
    unfinished = PrintJob.objects.unscoped(reason="health: the oldest unprinted job in any restaurant").filter(
        status__in=[PrintJob.PENDING, PrintJob.PROCESSING])
    oldest = unfinished.order_by("created_at").values_list("created_at", flat=True).first()
    if oldest is None:
        return True, "nothing waiting to print"
    minutes = int((now - oldest).total_seconds() // 60)
    waiting = unfinished.count()
    if now - oldest > PRINT_JOB_STUCK:
        return False, f"{waiting} job(s) waiting, the oldest for {minutes} min: is a print agent down?"
    return True, f"{waiting} job(s) waiting, the oldest for {minutes} min"


def detailed(now=None):
    """Every check, and whether the server is healthy: (body, status)."""
    now = now or timezone.now()
    checks = dict([
        _check("database", check_database),
        _check("cache", check_cache),
        _check("heartbeat", lambda: check_heartbeat(now)),
        _check("disk", check_disk),
        _check("swap", check_swap),
        _check("backup", lambda: check_backup(now)),
        _check("printing", lambda: check_printing(now)),
    ])
    healthy = all(c["ok"] is not False for c in checks.values())
    return {"status": "healthy" if healthy else "unhealthy", "checks": checks}, (200 if healthy else 503)
