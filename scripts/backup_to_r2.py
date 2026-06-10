#!/usr/bin/env python
"""
Back up the Rasova Postgres database to a PRIVATE Cloudflare R2 bucket.

  pg_dump  ->  gzip  ->  R2 (private bucket)  ->  prune backups older than N days

Why a separate PRIVATE bucket: the media bucket (rasova-media) is PUBLIC so logos
serve over the internet. A database dump must NEVER live in a public bucket — make
a second bucket (default: rasova-backups) and do NOT attach a public domain to it.

Setup (one time):
  1. In Cloudflare R2, create a bucket  rasova-backups  (leave it PRIVATE — no
     custom domain, no public dev URL).
  2. The R2 API token you already created has Object Read & Write — it works for
     this bucket too (same account).
  3. Add to .env (optional — these are the defaults):
        R2_BACKUP_BUCKET=rasova-backups
        R2_BACKUP_RETAIN_DAYS=30

Run manually:
    cd /home/ubuntu/rasova && .venv/bin/python scripts/backup_to_r2.py

Cron (nightly at 2am):
    0 2 * * * cd /home/ubuntu/rasova && .venv/bin/python scripts/backup_to_r2.py \
              >> /home/ubuntu/rasova/logs/backup.log 2>&1

Restore (see MEDIA_AND_BACKUPS.md):
    download the .sql.gz from R2  ->  gunzip  ->  psql < dump.sql
"""
import os
import sys
import gzip
import subprocess
import datetime

import django

# ── Boot Django so we can reuse DB + R2 settings ────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.conf import settings
import boto3

BUCKET      = os.getenv("R2_BACKUP_BUCKET", "rasova-backups")
RETAIN_DAYS = int(os.getenv("R2_BACKUP_RETAIN_DAYS", "30"))


def _fail(msg):
    print(f"[backup] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    db = settings.DATABASES["default"]
    if not settings.AWS_S3_ENDPOINT_URL or not settings.AWS_ACCESS_KEY_ID:
        _fail("R2 not configured (AWS_S3_ENDPOINT_URL / AWS_ACCESS_KEY_ID missing).")

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    key   = f"db/rasova_{stamp}.sql.gz"

    # 1 ── pg_dump (password via env so it never appears in the process list)
    env = dict(os.environ, PGPASSWORD=str(db.get("PASSWORD", "")))
    cmd = [
        "pg_dump",
        "-h", str(db.get("HOST") or "localhost"),
        "-p", str(db.get("PORT") or "5432"),
        "-U", str(db.get("USER") or ""),
        "--no-owner", "--no-privileges",
        str(db.get("NAME") or ""),
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True)
    if proc.returncode != 0:
        _fail("pg_dump failed: " + proc.stderr.decode("utf-8", "replace")[:500])

    blob = gzip.compress(proc.stdout)
    if len(blob) < 100:
        _fail("dump suspiciously small — aborting (DB empty or dump failed?).")

    # 2 ── upload to the PRIVATE R2 bucket
    s3 = boto3.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    s3.put_object(Bucket=BUCKET, Key=key, Body=blob,
                  ContentType="application/gzip")
    print(f"[backup] uploaded s3://{BUCKET}/{key}  ({len(blob)//1024} KB)")

    # 3 ── prune old backups
    cutoff  = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=RETAIN_DAYS)
    deleted = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix="db/"):
        for obj in page.get("Contents", []):
            if obj["LastModified"] < cutoff:
                s3.delete_object(Bucket=BUCKET, Key=obj["Key"])
                deleted += 1
    if deleted:
        print(f"[backup] pruned {deleted} backup(s) older than {RETAIN_DAYS} days")
    print("[backup] done.")


if __name__ == "__main__":
    main()
