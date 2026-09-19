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
import subprocess
import datetime

import django

# ── Boot Django so we can reuse DB + R2 settings ────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))
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

    # Stream pg_dump -> gzip -> R2, instead of buffering the whole dump in
    # this process's memory first. On a small box, `capture_output=True` +
    # `gzip.compress()` + a single put_object() meant: the entire uncompressed
    # dump had to fit in RAM, AND the final upload was capped at R2's 5GiB
    # single-PUT limit. Piping through two real OS processes means neither
    # limit applies — memory usage stays flat regardless of DB size, and
    # boto3's upload_fileobj automatically switches to multipart for
    # anything past its threshold, so there's no size ceiling at all here.
    env = dict(os.environ, PGPASSWORD=str(db.get("PASSWORD", "")))
    dump_cmd = [
        "pg_dump",
        "-h", str(db.get("HOST") or "localhost"),
        "-p", str(db.get("PORT") or "5432"),
        "-U", str(db.get("USER") or ""),
        "--no-owner", "--no-privileges",
        str(db.get("NAME") or ""),
    ]
    dump_proc = subprocess.Popen(dump_cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    gzip_proc = subprocess.Popen(["gzip"], stdin=dump_proc.stdout, stdout=subprocess.PIPE)
    dump_proc.stdout.close()  # let dump_proc receive SIGPIPE if gzip_proc dies first

    s3 = boto3.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    s3.upload_fileobj(gzip_proc.stdout, BUCKET, key, ExtraArgs={"ContentType": "application/gzip"})
    gzip_proc.stdout.close()

    gzip_proc.wait()
    dump_proc.wait()
    if dump_proc.returncode != 0:
        s3.delete_object(Bucket=BUCKET, Key=key)
        _fail("pg_dump failed: " + dump_proc.stderr.read().decode("utf-8", "replace")[:500])
    if gzip_proc.returncode != 0:
        s3.delete_object(Bucket=BUCKET, Key=key)
        _fail(f"gzip exited {gzip_proc.returncode}")

    size = s3.head_object(Bucket=BUCKET, Key=key)["ContentLength"]
    if size < 100:
        s3.delete_object(Bucket=BUCKET, Key=key)
        _fail(f"dump suspiciously small ({size} bytes) — aborting (DB empty or dump failed?).")
    print(f"[backup] uploaded s3://{BUCKET}/{key}  ({size // 1024} KB)")

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
