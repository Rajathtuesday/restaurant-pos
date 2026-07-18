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

Script: **`scripts/backup_to_r2.py`** — `pg_dump → gzip → private R2 bucket → prune old`.

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
cd /home/ubuntu/rasova && .venv/bin/python scripts/backup_to_r2.py
```

### Automate it (nightly 2am)
```bash
crontab -e
# add:
0 2 * * * cd /home/ubuntu/rasova && .venv/bin/python scripts/backup_to_r2.py >> /home/ubuntu/rasova/logs/backup.log 2>&1
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
