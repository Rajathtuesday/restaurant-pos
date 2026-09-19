# Media Storage & Database Backups (Cloudflare R2)

> Operational runbook. Media (logos, menu images) and DB backups both live on
> **Cloudflare R2** — chosen over AWS S3 because R2 has **zero egress fees**
> (free downloads) plus a 10 GB free tier.

---

## 1. The two buckets — and the one rule that matters

| Bucket | Visibility | Holds | Served via |
|---|---|---|---|
| `rasova-media` | **PUBLIC** | logos, menu images | `https://media.rasova.net/...` (custom domain, CDN-cached) |
| `rasova-backups` | **PRIVATE** | `pg_dump` database backups | nothing — API access only |

> ⚠️ **THE RULE:** media is public so images load in browsers. **Database backups
> must NEVER go in a public bucket** — a public DB dump is a full data breach.
> Keep `rasova-backups` private: no custom domain, no public dev URL.

---

## 2. How media works

```
User uploads a logo in Setup
        │
        ▼
Django (S3Boto3Storage)  ──PUT──►  R2 bucket: rasova-media/tenant_logos/xxxx.jpg
        │
        ▼
tenant.logo.url  ─►  https://media.rasova.net/tenant_logos/xxxx.jpg
        │
        ▼
Browser loads it via the custom domain (Cloudflare CDN, free egress)
```

Every uploaded file (logos now, menu images, etc.) goes to R2 automatically — no
code changes needed per upload.

### Config — all env vars (in server `.env`)
```bash
AWS_STORAGE_BUCKET_NAME=rasova-media
AWS_ACCESS_KEY_ID=<32-char R2 access key id>
AWS_SECRET_ACCESS_KEY=<R2 secret>
AWS_S3_ENDPOINT_URL=https://836c606fc06525ba405b92c49ff23845.r2.cloudflarestorage.com
AWS_S3_REGION_NAME=auto
AWS_S3_CUSTOM_DOMAIN=media.rasova.net
```
Driven entirely from `core/settings.py` (the `if _AWS_BUCKET:` block). If
`AWS_STORAGE_BUCKET_NAME` is unset, it **falls back to local disk** (dev mode).

### Why we moved (the bug that started it)
In production (`DEBUG=False`) Django only serves `/media/` when `DEBUG=True`, and
WhiteNoise serves static files + `public/`, **not** `MEDIA_ROOT`. So locally-stored
uploads returned **404** on the live site. They also die if the server is replaced.
R2 fixes both **serving** and **durability**.

---

## 3. Database backups

Script: **`scripts/backup/backup_to_r2.py`** — `pg_dump → gzip → private R2 bucket → prune old`.

### One-time setup
1. In Cloudflare R2, create bucket **`rasova-backups`** — leave it **PRIVATE**
   (no custom domain, no public dev URL).
2. (Optional) add to `.env`:
   ```bash
   R2_BACKUP_BUCKET=rasova-backups
   R2_BACKUP_RETAIN_DAYS=30
   ```

### Run it
```bash
cd /home/ubuntu/rasova && .venv/bin/python scripts/backup/backup_to_r2.py
```

### Automate it (nightly 2am)
```bash
crontab -e
# add:
0 2 * * * cd /home/ubuntu/rasova && .venv/bin/python scripts/backup/backup_to_r2.py >> /home/ubuntu/rasova/logs/backup.log 2>&1
```

The script keeps the last `RETAIN_DAYS` (default 30) of backups and deletes older
ones automatically.

---

## 4. Restore from a backup ⟵ test this BEFORE you need it

```bash
# 1. List available backups
.venv/bin/python -c "
import boto3; from django.conf import settings; import django
django.setup()
s3 = boto3.client('s3', endpoint_url=settings.AWS_S3_ENDPOINT_URL,
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY, region_name='auto')
for o in s3.list_objects_v2(Bucket='rasova-backups', Prefix='db/').get('Contents', []):
    print(o['Key'], o['Size'])
"

# 2. Download one (replace the date)
.venv/bin/python -c "
import boto3; from django.conf import settings; import django
django.setup()
s3 = boto3.client('s3', endpoint_url=settings.AWS_S3_ENDPOINT_URL,
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY, region_name='auto')
s3.download_file('rasova-backups', 'db/rasova_2026-06-11_0200.sql.gz', '/tmp/restore.sql.gz')
print('downloaded')
"

# 3. Restore into a database (DANGER: overwrites — restore into a fresh/spare DB first to verify)
gunzip -c /tmp/restore.sql.gz | psql -h localhost -U rasova rasova
```

> A backup you've never restored is a hope, not a backup. Do a restore drill once.

---

## 4b. WAL archiving — closing the "up to 24 hours lost" gap

The nightly `pg_dump` above only ever gives you last night's data. If the
server dies at 1:59am, the day's orders since 2am yesterday are gone. WAL
(Write-Ahead Log) archiving fixes that by continuously streaming every
change to R2, not just once a night — see the ELI5 walkthrough in
`md_files/` for the full explanation with the code annotated line by line.

**Three moving parts**, all already in `scripts/`:

| Script | Runs | Job |
|---|---|---|
| `archive_wal_to_r2.py` | Automatically, by Postgres itself | Ships each finished WAL segment to `rasova-backups/wal/` |
| `base_backup_to_r2.py` | Weekly, cron | Full physical snapshot to `rasova-backups/base/` — the anchor WAL gets replayed on top of |
| `restore_wal_from_r2.py` | Only during a recovery | Fetches archived WAL segments back, on demand |

> **Verified 2026-09-19** via a real Docker drill (throwaway Postgres 16, real base
> backup, real WAL archiving, real point-in-time restore): the mechanism works exactly
> as documented below, data committed before the target time survives, data committed
> after does not, down to individual-transaction precision. The drill also caught a real
> bug before it ever reached this server: the archive destination was owned by root with
> no write access for the `postgres` OS user, so archiving failed silently on every
> single attempt while WAL piled up locally, undetected. **This is exactly the failure
> mode `check_wal_archiving_health.py` (below) exists to catch** — confirm step 3 below
> passes before trusting this is actually running, don't assume it's working just
> because you didn't see an error.

### One-time server setup

1. Add to `postgresql.conf` (path is usually `/etc/postgresql/<version>/main/postgresql.conf`):
   ```ini
   wal_level = replica
   archive_mode = on
   archive_command = '/home/ubuntu/rasova/.venv/bin/python /home/ubuntu/rasova/scripts/backup/archive_wal_to_r2.py %p %f'
   archive_timeout = 300
   ```
   `wal_level = replica` and `archive_mode = on` **require a full Postgres restart**
   (not just reload) to take effect — plan this for a quiet moment.
2. Restart Postgres: `sudo systemctl restart postgresql`
3. Verify it took: `sudo -u postgres psql -c "SHOW wal_level; SHOW archive_mode;"`
4. Watch it actually archive something: `sudo -u postgres psql -c "SELECT pg_switch_wal();"` forces
   one segment to close immediately, then check `rasova-backups/wal/` in the R2 dashboard for a new object.
5. Add the weekly base-backup cron:
   ```bash
   crontab -e
   # add:
   0 3 * * 0 cd /home/ubuntu/rasova && .venv/bin/python scripts/backup/base_backup_to_r2.py >> /home/ubuntu/rasova/logs/base_backup.log 2>&1
   ```
6. Run `base_backup_to_r2.py` once manually right now too — WAL archived before
   the first base backup exists is not useful on its own, you need the anchor.
7. Add the health check to cron too, and actually watch it once before trusting it:
   ```bash
   */15 * * * * cd /home/ubuntu/rasova && .venv/bin/python scripts/backup/check_wal_archiving_health.py >> /home/ubuntu/rasova/logs/wal_health.log 2>&1
   ```
   Confirm `archive_command` can genuinely run as the `postgres` OS user before
   walking away — permissions are the one failure mode that fails completely silently
   otherwise (see the drill note above). Check the R2 bucket's `wal/` prefix directly
   after a few minutes, or just watch `wal_health.log` for the first "OK" line.

### Restoring to a point in time (not just "last night")

```bash
# 1. Stop Postgres
sudo systemctl stop postgresql

# 2. Clear the data directory and extract the latest base backup into it
#    (download rasova_base_<stamp>.tar.gz from R2 first)
sudo rm -rf /var/lib/postgresql/<version>/main/*
sudo tar -xzf rasova_base_<stamp>.tar.gz -C /var/lib/postgresql/<version>/main/

# 3. Tell Postgres to recover, and how to fetch the WAL it needs
sudo touch /var/lib/postgresql/<version>/main/recovery.signal
# in postgresql.auto.conf:
restore_command = '/home/ubuntu/rasova/.venv/bin/python /home/ubuntu/rasova/scripts/backup/restore_wal_from_r2.py %f %p'
recovery_target_time = '2026-09-19 14:30:00'   # omit this line to replay everything available

# 4. Start Postgres — it replays WAL automatically, then comes up caught-up
sudo systemctl start postgresql
```

---

## 5. Troubleshooting — "the logo / image isn't showing"

Work down this list:

1. **Is it saved?** `tenant.logo.url` should print a `https://media.rasova.net/...` URL
   (not `/media/...`). If it's `/media/...`, the R2 env vars aren't set / loaded.
2. **Is the file actually in R2?** Check the bucket in the Cloudflare dashboard, or
   open the `media.rasova.net/...` URL directly.
   - URL 404s but file *is* in the bucket → the **custom domain isn't Active**
     (R2 → rasova-media → Settings → Custom Domains → connect `media.rasova.net`).
3. **`Credential access key has length 0`** on upload → `AWS_ACCESS_KEY_ID` is
   empty in `.env`. Add the R2 token keys; verify with:
   ```bash
   python manage.py shell -c "from django.conf import settings; print(len(settings.AWS_ACCESS_KEY_ID))"  # want 32
   ```
4. **Uploads silently go nowhere / hit AWS** → `AWS_S3_ENDPOINT_URL` missing, so the
   backend talks to real AWS S3 instead of R2. Set the R2 endpoint.
5. After changing `.env`, **restart gunicorn** so the web app re-reads it:
   `pkill -HUP -f 'gunicorn: master'`

---

## 6. Hosting note (AWS vs Cloudflare)

Cloudflare is **not** a place to run the Django app — it has no EC2-style VM for a
long-running Python + Postgres + Celery stack (Cloudflare = R2, DNS, CDN, SSL,
Workers/edge). So:

- **Storage / CDN / DNS / SSL → Cloudflare** (R2 done; DNS already on Cloudflare). ✅
- **The server (Django VM) → stays a VM** (AWS EC2 today, or a cheaper India VPS later).

The only thing we migrated to Cloudflare is what it's genuinely best at: **object
storage**. The compute stays on a real server.
