#!/usr/bin/env python
"""
Take a full physical base backup of Postgres and upload it to R2.

Why this exists separately from backup_to_r2.py: that script's `pg_dump` is
a LOGICAL backup, a SQL script that recreates the data, fine on its own for
a plain restore, but WAL segments (see archive_wal_to_r2.py) are only
meaningful as changes applied ON TOP OF a physical copy of the real data
files. You cannot replay WAL against a pg_dump. This script produces that
physical copy, so a point-in-time recovery has something to start from.

Run weekly — WAL archives fill the gap between base backups, so the more
often this runs, the less WAL a future recovery has to replay:

Cron (Sunday 3am):
    0 3 * * 0 cd /home/ubuntu/rasova && .venv/bin/python scripts/base_backup_to_r2.py \
              >> /home/ubuntu/rasova/logs/base_backup.log 2>&1
"""
import os
import sys
import tempfile
import datetime
import subprocess

import django

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.conf import settings
import boto3

BUCKET       = os.getenv("R2_BACKUP_BUCKET", "rasova-backups")
RETAIN_WEEKS = int(os.getenv("R2_BASE_BACKUP_RETAIN_WEEKS", "4"))


def _fail(msg):
    print(f"[base-backup] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    db = settings.DATABASES["default"]
    if not settings.AWS_S3_ENDPOINT_URL or not settings.AWS_ACCESS_KEY_ID:
        _fail("R2 not configured (AWS_S3_ENDPOINT_URL / AWS_ACCESS_KEY_ID missing).")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    env = dict(os.environ, PGPASSWORD=str(db.get("PASSWORD", "")))

    with tempfile.TemporaryDirectory() as tmp:
        cmd = [
            "pg_basebackup",
            "-h", str(db.get("HOST") or "localhost"),
            "-p", str(db.get("PORT") or "5432"),
            "-U", str(db.get("USER") or ""),
            "-D", tmp,
            "-F", "tar", "-z",        # write one gzip-compressed tar, not raw files
            "-X", "fetch",            # bundle in the WAL made *during* the backup itself
            "--checkpoint=fast",      # don't wait for Postgres's normal slow checkpoint
        ]
        proc = subprocess.run(cmd, env=env, capture_output=True)
        if proc.returncode != 0:
            _fail("pg_basebackup failed: " + proc.stderr.decode("utf-8", "replace")[:500])

        base_file = os.path.join(tmp, "base.tar.gz")
        if not os.path.exists(base_file):
            _fail("pg_basebackup didn't produce base.tar.gz as expected.")

        s3 = boto3.client(
            "s3",
            endpoint_url=settings.AWS_S3_ENDPOINT_URL,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        key = f"base/rasova_base_{stamp}.tar.gz"
        with open(base_file, "rb") as f:
            s3.put_object(Bucket=BUCKET, Key=key, Body=f.read(), ContentType="application/gzip")
        size_mb = os.path.getsize(base_file) / (1024 * 1024)
        print(f"[base-backup] uploaded s3://{BUCKET}/{key}  ({size_mb:.1f} MB)")

        # prune old base backups
        cutoff    = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(weeks=RETAIN_WEEKS)
        paginator = s3.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=BUCKET, Prefix="base/"):
            for obj in page.get("Contents", []):
                if obj["LastModified"] < cutoff:
                    s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
                    deleted += 1
        if deleted:
            print(f"[base-backup] pruned {deleted} base backup(s) older than {RETAIN_WEEKS} weeks")

        # prune WAL segments no longer reachable from any base backup we still keep —
        # anything archived before `cutoff` predates our oldest surviving photo, so a
        # recovery could never need it. Without this step, WAL would grow forever.
        deleted_wal = 0
        for page in paginator.paginate(Bucket=BUCKET, Prefix="wal/"):
            for obj in page.get("Contents", []):
                if obj["LastModified"] < cutoff:
                    s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
                    deleted_wal += 1
        if deleted_wal:
            print(f"[base-backup] pruned {deleted_wal} WAL segment(s) older than {RETAIN_WEEKS} weeks")
    print("[base-backup] done.")


if __name__ == "__main__":
    main()
