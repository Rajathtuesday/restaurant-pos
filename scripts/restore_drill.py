#!/usr/bin/env python
"""
Restore DRILL — proves a backup is actually restorable, safely.

Downloads the LATEST backup from the private R2 bucket (rasova-backups),
restores it into a THROWAWAY database (NEVER production), runs sanity checks,
then drops the throwaway DB.

  R2 backup  ->  download  ->  gunzip  ->  fresh temp DB  ->  psql restore
             ->  count tables + key rows  ->  drop temp DB

Usage:
    python scripts/restore_drill.py                 # latest backup, full drill, cleans up
    python scripts/restore_drill.py --keep          # keep the temp DB to inspect it
    python scripts/restore_drill.py --file db/rasova_2026-06-11_0137.sql.gz   # a specific backup

Needs: the DB user must be able to CREATEDB. If not, grant it once as the
postgres superuser:  ALTER USER rasova CREATEDB;
(or run this script as the postgres user.)
"""
import os
import sys
import gzip
import argparse
import subprocess

import django

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from django.conf import settings
import boto3

BUCKET  = os.getenv("R2_BACKUP_BUCKET", "rasova-backups")
TEST_DB = os.getenv("RESTORE_DRILL_DB", "rasova_restore_drill")
TMP_GZ  = "/tmp/rasova_drill.sql.gz"
TMP_SQL = "/tmp/rasova_drill.sql"


def _r2():
    return boto3.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name="auto",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="specific backup key (default: latest)")
    ap.add_argument("--keep", action="store_true", help="keep the temp DB for inspection")
    args = ap.parse_args()

    db   = settings.DATABASES["default"]
    prod = str(db.get("NAME") or "")

    # ── Safety: never touch production ──────────────────────────────────────────
    if TEST_DB == prod:
        sys.exit(f"[drill] REFUSING — drill DB name equals production ({prod}).")

    s3 = _r2()

    # 1 ── pick a backup
    if args.file:
        key = args.file
    else:
        objs = s3.list_objects_v2(Bucket=BUCKET, Prefix="db/").get("Contents", [])
        if not objs:
            sys.exit("[drill] no backups found in R2.")
        key = sorted(objs, key=lambda o: o["LastModified"])[-1]["Key"]
    print(f"[drill] backup: {key}")

    # 2 ── download + gunzip
    s3.download_file(BUCKET, key, TMP_GZ)
    with gzip.open(TMP_GZ, "rb") as fin, open(TMP_SQL, "wb") as fout:
        fout.write(fin.read())
    print(f"[drill] downloaded + unzipped ({os.path.getsize(TMP_SQL) // 1024} KB SQL)")

    # 3 ── connection args (password via env, never on the command line)
    env  = dict(os.environ, PGPASSWORD=str(db.get("PASSWORD", "")))
    conn = [
        "-h", str(db.get("HOST") or "localhost"),
        "-p", str(db.get("PORT") or "5432"),
        "-U", str(db.get("USER") or ""),
    ]

    def run(cmd, **kw):
        return subprocess.run(cmd, env=env, **kw)

    # 4 ── fresh throwaway DB
    run(["dropdb", *conn, "--if-exists", TEST_DB], capture_output=True)
    r = run(["createdb", *conn, TEST_DB], capture_output=True)
    if r.returncode != 0:
        sys.exit("[drill] createdb failed — does the DB user have CREATEDB?\n"
                 + r.stderr.decode("utf-8", "replace")[:400])
    print(f"[drill] created throwaway DB: {TEST_DB}")

    # 5 ── restore the dump
    with open(TMP_SQL, "rb") as f:
        r = run(["psql", *conn, "-q", "-v", "ON_ERROR_STOP=1", TEST_DB],
                stdin=f, capture_output=True)
    if r.returncode != 0:
        print("[drill] ❌ RESTORE FAILED:\n" + r.stderr.decode("utf-8", "replace")[:1000])
        if not args.keep:
            run(["dropdb", *conn, "--if-exists", TEST_DB], capture_output=True)
        sys.exit(1)
    print("[drill] restore completed without errors")

    # 6 ── sanity check: tables present + key rows loaded
    sql = (
        "SELECT 'tables', count(*) FROM information_schema.tables WHERE table_schema='public' "
        "UNION ALL SELECT 'tenants', count(*) FROM tenants_tenant "
        "UNION ALL SELECT 'outlets', count(*) FROM tenants_outlet "
        "UNION ALL SELECT 'orders',  count(*) FROM orders_order;"
    )
    r = run(["psql", *conn, "-t", "-A", "-F", " = ", TEST_DB, "-c", sql],
            capture_output=True)
    print("[drill] sanity check:")
    for line in r.stdout.decode("utf-8", "replace").strip().splitlines():
        print("   ", line)

    # 7 ── cleanup
    if args.keep:
        print(f"[drill] keeping {TEST_DB} (--keep). Drop later:  dropdb {' '.join(conn)} {TEST_DB}")
    else:
        run(["dropdb", *conn, "--if-exists", TEST_DB], capture_output=True)
        print(f"[drill] dropped {TEST_DB}")
    for f in (TMP_GZ, TMP_SQL):
        try:
            os.remove(f)
        except OSError:
            pass

    print("[drill] ✅ SUCCESS — the backup is valid and restorable.")


if __name__ == "__main__":
    main()
