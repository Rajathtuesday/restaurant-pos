#!/usr/bin/env python
"""
Catch a stalled WAL archiver before it fills the server's disk.

Why this exists: Postgres will NOT recycle a WAL segment until archive_command
succeeds for it. If archiving stalls for any reason (R2 outage, rotated
credentials, a bug), segments just keep piling up in pg_wal on the server's
own disk instead. This box is a small, already-tight t3.micro (see the infra
notes), so "archiving quietly broke three days ago" is a real way this server
could actually run out of disk and stop accepting writes, not a theoretical
risk. This script checks Postgres's own bookkeeping (`pg_stat_archiver`) plus
the actual pg_wal directory, and exits non-zero the moment something looks off.

Two independent signals, on purpose — either one alone can miss things:
  1. `pg_stat_archiver`: Postgres's own record of when it last successfully
     archived a segment, and when it last failed. Precise, but only reflects
     what Postgres itself has tried, not what's piling up on disk right now.
  2. Raw pg_wal directory size: a direct, Postgres-independent check on the
     thing that actually threatens the disk.

Run this on a schedule and read its output/exit code, it does not send an
alert on its own (no email/Slack wired up — that's a separate decision):

Cron (every 15 min):
    */15 * * * * cd /home/ubuntu/rasova && .venv/bin/python scripts/check_wal_archiving_health.py \
                 >> /home/ubuntu/rasova/logs/wal_health.log 2>&1
"""
import os
import sys
import datetime
import subprocess

import django

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.db import connection
from django.conf import settings

# If archiving hasn't succeeded in longer than this, something's wrong.
# Should be a few multiples of archive_timeout (300s in postgresql.conf),
# not exactly equal to it — a single slow segment isn't an incident.
STALE_AFTER_SECONDS = int(os.getenv("WAL_ARCHIVE_STALE_SECONDS", "1800"))  # 30 min

# pg_wal directory itself — if this many uncollected 16MB segments pile up
# (default 50 = ~800MB), that's a backlog worth knowing about on a small box.
PG_WAL_SEGMENT_WARN_COUNT = int(os.getenv("WAL_LOCAL_SEGMENT_WARN_COUNT", "50"))

# Postgres's data directory. Only needs overriding if it's not the standard
# Debian/Ubuntu layout (e.g. a different major version, or a non-default install).
PG_DATA_DIR_DEFAULT = "/var/lib/postgresql/18/main"


def _warn(msg):
    print(f"[wal-health] WARNING: {msg}")


def check_pg_stat_archiver():
    """Ask Postgres itself how archiving is actually going."""
    with connection.cursor() as cur:
        cur.execute("""
            SELECT archived_count, last_archived_time,
                   failed_count, last_failed_time, last_failed_wal
            FROM pg_stat_archiver
        """)
        row = cur.fetchone()

    if row is None:
        _warn("pg_stat_archiver returned no row — unexpected, investigate.")
        return False

    archived_count, last_archived_time, failed_count, last_failed_time, last_failed_wal = row
    now = datetime.datetime.now(datetime.timezone.utc)
    ok = True

    if last_archived_time is None:
        _warn("no WAL segment has ever been archived yet — expected right after setup, "
              "otherwise archive_mode may not actually be on.")
        ok = False
    else:
        age = (now - last_archived_time).total_seconds()
        if age > STALE_AFTER_SECONDS:
            _warn(f"last successful archive was {age/60:.0f} min ago "
                  f"(threshold {STALE_AFTER_SECONDS/60:.0f} min) — archiving may have stalled.")
            ok = False

    if last_failed_time is not None and (last_archived_time is None or last_failed_time > last_archived_time):
        _warn(f"most recent archive attempt FAILED (wal segment: {last_failed_wal}, "
              f"at {last_failed_time}) and no successful archive since — check archive_command "
              f"can actually run (permissions, .env readable by the postgres OS user, R2 reachable).")
        ok = False

    print(f"[wal-health] archived_count={archived_count} failed_count={failed_count} "
          f"last_archived={last_archived_time}")
    return ok


def check_local_wal_backlog():
    """Independent of Postgres's own bookkeeping: is pg_wal itself piling up?"""
    # `SHOW data_directory` would need the app role to hold pg_read_all_settings,
    # a privilege it has no other reason to carry. The data directory is static
    # server config, not something that needs a live DB query — read it from an
    # env var instead (falls back to the standard Debian/Ubuntu layout).
    data_dir = os.getenv("PG_DATA_DIR", PG_DATA_DIR_DEFAULT)
    pg_wal_dir = os.path.join(data_dir, "pg_wal")
    if not os.path.isdir(pg_wal_dir):
        _warn(f"couldn't find pg_wal at {pg_wal_dir} — check path (this script must run "
              f"on the DB server itself, not a remote client).")
        return False

    segments = [f for f in os.listdir(pg_wal_dir) if len(f) == 24 and all(c in "0123456789ABCDEF" for c in f)]
    count = len(segments)
    print(f"[wal-health] {count} WAL segment(s) currently sitting in {pg_wal_dir}")

    if count > PG_WAL_SEGMENT_WARN_COUNT:
        approx_mb = count * 16
        _warn(f"{count} segments locally (~{approx_mb} MB) exceeds the warn threshold "
              f"({PG_WAL_SEGMENT_WARN_COUNT}) — Postgres is holding onto WAL it couldn't "
              f"archive yet. On a small disk this can become a real outage if it keeps growing.")
        return False
    return True


def main():
    ok_archiver = check_pg_stat_archiver()
    ok_local    = check_local_wal_backlog()

    if ok_archiver and ok_local:
        print("[wal-health] OK — archiving is healthy.")
        sys.exit(0)
    else:
        print("[wal-health] ONE OR MORE CHECKS FAILED — see WARNING lines above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
