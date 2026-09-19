#!/usr/bin/env python
"""
Fetch one archived WAL segment back from R2 during a recovery.

The mirror image of archive_wal_to_r2.py. Postgres calls this once per WAL
segment it needs while replaying a recovery, via `restore_command` in
postgresql.conf — that setting only does anything while a `recovery.signal`
file exists in the data directory (see MEDIA_AND_BACKUPS.md for the full
recovery procedure). Postgres calling this and getting a non-zero exit is
NORMAL at the very end of a recovery, that's how it knows it has replayed
every segment that exists and can stop looking for more.

Postgres calls this as:
    restore_wal_from_r2.py  <wal filename (%f)>  <path to write it to (%p)>

Wire-up, only needed WHILE recovering (postgresql.conf or postgresql.auto.conf):
    restore_command = '/home/ubuntu/rasova/.venv/bin/python /home/ubuntu/rasova/scripts/restore_wal_from_r2.py %f %p'
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
        sys.exit("usage: restore_wal_from_r2.py <wal_filename> <destination_path>")
    wal_filename, dest_path = sys.argv[1], sys.argv[2]

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=f"wal/{wal_filename}.gz")
        compressed = obj["Body"].read()
    except Exception:
        sys.exit(1)  # "not found" — Postgres reads this as "recovery is complete"

    with open(dest_path, "wb") as f:
        f.write(gzip.decompress(compressed))


if __name__ == "__main__":
    main()
