#!/usr/bin/env python
"""
Archive one Postgres WAL segment to the private R2 backup bucket.

Postgres calls this itself, automatically, once per WAL segment (every time
a 16MB segment fills up, or every `archive_timeout` seconds, whichever comes
first — see postgresql.conf). Wired in via `archive_command`. Postgres will
NOT reuse or delete a WAL segment until this script exits 0 for it, so a
bug here that always fails will fill the server's disk with backlogged WAL
files. Keep this simple, fail loudly, never swallow an error.

Postgres calls this as:
    archive_wal_to_r2.py  <full path to the WAL file (%p)>  <just the filename (%f)>

Wire-up (postgresql.conf, requires a full Postgres RESTART, not just reload):
    wal_level = replica
    archive_mode = on
    archive_command = '/home/ubuntu/rasova/.venv/bin/python /home/ubuntu/rasova/scripts/archive_wal_to_r2.py %p %f'
    archive_timeout = 300
"""
import os
import sys
import gzip

import django

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.conf import settings
import boto3

BUCKET = os.getenv("R2_BACKUP_BUCKET", "rasova-backups")


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: archive_wal_to_r2.py <wal_path> <wal_filename>")
    wal_path, wal_filename = sys.argv[1], sys.argv[2]

    if not settings.AWS_S3_ENDPOINT_URL or not settings.AWS_ACCESS_KEY_ID:
        sys.exit("[wal-archive] R2 not configured — refusing to silently drop a WAL segment.")

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    # .gz: a forced-early segment (archive_timeout) is still a fixed 16MB file,
    # mostly zero-padding on a quiet night — gzip crushes that down to almost nothing.
    key = f"wal/{wal_filename}.gz"

    # Postgres can call this again for a segment it already archived (e.g. after
    # a crash mid-archive) — skip re-upload rather than erroring, and still exit 0.
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return
    except Exception:
        pass  # not found yet — proceed to upload

    with open(wal_path, "rb") as f:
        blob = gzip.compress(f.read())
    s3.put_object(Bucket=BUCKET, Key=key, Body=blob, ContentType="application/gzip")


if __name__ == "__main__":
    main()
