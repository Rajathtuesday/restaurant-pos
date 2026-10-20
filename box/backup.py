"""
Backups on a box: a full copy of the database every night, kept for
KEEP_DAYS, in BOX_BACKUP_DIR (the installer points it at a second drive or a
USB stick, mounted into the box as /backups).

  * pg_dump writes a consistent snapshot while billing carries on (it reads
    one moment of the database; nothing is locked for the staff).
  * It writes to a temporary name, checks the copy can be read back
    (pg_restore --list), and only then gives it its real name, so a power
    cut or a full disk never leaves something that looks like a backup but
    isn't.
  * Only one backup runs at a time (a Postgres advisory lock).
  * The result, good or bad, is recorded in BoxState and shown to the
    owner: a backup that silently stopped working is the worst kind.

Restoring is box_restore (the app is stopped first, by the box's own
script: rasova-box restore <file>).
"""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone

KEEP_DAYS = 14
NAME = re.compile(r"^rasova-\d{8}-\d{6}\.dump$")
LOCK_ID = 727701


class BackupError(Exception):
    pass


def backup_dir():
    return Path(getattr(settings, "BOX_BACKUP_DIR", "/backups"))


def _pg_env():
    db = settings.DATABASES["default"]
    env = dict(os.environ)
    env["PGPASSWORD"] = str(db.get("PASSWORD") or "")
    args = ["-h", str(db.get("HOST") or "localhost"), "-p", str(db.get("PORT") or 5432),
            "-U", str(db.get("USER") or "postgres")]
    return env, args, str(db["NAME"])


def list_backups():
    folder = backup_dir()
    if not folder.exists():
        return []
    files = sorted((p for p in folder.iterdir() if NAME.match(p.name)), key=lambda p: p.name, reverse=True)
    return [{"name": p.name, "bytes": p.stat().st_size, "path": str(p)} for p in files]


def run_backup(keep=KEEP_DAYS, now=None):
    """Make one backup; returns its path. Records the outcome either way."""
    from box.models import BoxState
    now = now or timezone.now()
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_ID])
        if not cur.fetchone()[0]:
            raise BackupError("A backup is already running.")
    try:
        folder = backup_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise BackupError(f"The backup drive isn't there or can't be written ({e}).")
        last = BoxState.get().last_backup_bytes or 0
        free = shutil.disk_usage(folder).free
        if last and free < last * 2:
            raise BackupError(f"The backup drive is nearly full ({free // (1024 * 1024)} MB free).")

        stamp = timezone.localtime(now).strftime("%Y%m%d-%H%M%S")
        final = folder / f"rasova-{stamp}.dump"
        tmp = folder / f".rasova-{stamp}.dump.partial"
        env, args, name = _pg_env()
        made = subprocess.run(["pg_dump", "-Fc", *args, "-d", name, "-f", str(tmp)],
                              env=env, capture_output=True, text=True, timeout=3600)
        if made.returncode != 0:
            tmp.unlink(missing_ok=True)
            raise BackupError(f"pg_dump failed: {made.stderr.strip()[:300]}")
        check = subprocess.run(["pg_restore", "--list", str(tmp)], capture_output=True, text=True, timeout=600)
        if check.returncode != 0 or "TABLE DATA" not in check.stdout:
            tmp.unlink(missing_ok=True)
            raise BackupError("The backup could not be read back, so it was thrown away.")
        tmp.replace(final)
        _carry_licence(folder)
        for old in list_backups()[keep:]:
            Path(old["path"]).unlink(missing_ok=True)
        _record(ok=True, now=now, path=final, size=final.stat().st_size)
        return final
    except BackupError as e:
        _record(ok=False, now=now, error=str(e))
        raise
    except Exception as e:
        _record(ok=False, now=now, error=f"{type(e).__name__}: {e}"[:500])
        raise BackupError(str(e))
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [LOCK_ID])


LICENCE_COPY = "license.json"


def _carry_licence(folder):
    """A copy of the licence goes with the backups: if the box dies, its
    replacement gets the backup and the licence from the same drive
    (box_restore puts it back: license.adopt)."""
    from box.license import license_path
    source = license_path()
    if not source.exists():
        return
    tmp = folder / ".license.json.partial"
    shutil.copyfile(source, tmp)
    tmp.replace(folder / LICENCE_COPY)


def _record(ok, now, path=None, size=None, error=""):
    from box.models import BoxState
    fields = {"last_backup_at": now, "last_backup_ok": ok, "last_backup_error": error[:500]}
    if ok:
        fields.update(last_backup_file=path.name, last_backup_bytes=size)
    BoxState.get()
    BoxState.objects.filter(pk=1).update(**fields)


def restore(path, database=None):
    """Replace the database with a backup. The app must be stopped (the
    box's script does that).

    The backup is loaded into a NEW, empty database first, and only when
    that has fully worked is it swapped in by renaming. So:
      * a failed restore (a damaged file, a full disk) changes nothing;
      * nothing made after the backup is left behind (a table that a newer
        version added before its update was rolled back would otherwise
        stay, and trip that update the next time);
      * the database it replaced is kept as <name>_before_restore, the undo
        for restoring the wrong file (one level: the next restore replaces it).
    """
    path = Path(path)
    if not path.exists() or not NAME.match(path.name):
        raise BackupError("That isn't one of the box's backups.")
    env, args, name = _pg_env()
    name = database or name
    incoming, previous = f"{name}_restoring", f"{name}_before_restore"

    def sql(*statements):
        command = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", *args, "-d", "postgres"]
        for statement in statements:
            command += ["-c", statement]
        done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=600)
        if done.returncode != 0:
            raise BackupError(f"psql failed: {done.stderr.strip()[:300]}")

    sql(f'DROP DATABASE IF EXISTS "{incoming}"', f'CREATE DATABASE "{incoming}"')
    done = subprocess.run(["pg_restore", "--no-owner", "--single-transaction", *args, "-d", incoming, str(path)],
                          env=env, capture_output=True, text=True, timeout=3600)
    if done.returncode != 0:
        sql(f'DROP DATABASE IF EXISTS "{incoming}"')
        raise BackupError(f"pg_restore failed, nothing was changed: {done.stderr.strip()[:300]}")

    if connection.settings_dict["NAME"] == name:
        connection.close()                  # this process lets go of it too
    sql(f'DROP DATABASE IF EXISTS "{previous}"')
    for attempt in range(10):
        # Anything still connected is closed first; a rename can't happen
        # under an open connection. Both renames are one transaction: the
        # box is never left without a database called <name>.
        sql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{name}' AND pid <> pg_backend_pid()")
        try:
            sql(f'BEGIN; ALTER DATABASE "{name}" RENAME TO "{previous}"; '
                f'ALTER DATABASE "{incoming}" RENAME TO "{name}"; COMMIT;')
            return
        except BackupError:
            if attempt == 9:
                raise
            time.sleep(1)
