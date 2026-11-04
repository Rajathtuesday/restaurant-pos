#!/bin/bash
# Rasova production deploy script
set -e

APP_DIR=/home/ubuntu/rasova
cd $APP_DIR
source .venv/bin/activate

# PDF bills, invoices and purchase orders need WeasyPrint, and WeasyPrint needs
# Pango from the system (docs/DEPLOY.md, 2.2). Without it every PDF fails, and
# nothing else says so: Rasova starts fine and only prints a warning. That hid
# a missing Pango on this server until 4 Nov 2026. Checked before anything
# changes, so a failed check leaves the running version as it was.
if ! python -c "import weasyprint" >/dev/null 2>&1; then
    echo "=== STOP: WeasyPrint can't load, so PDFs would fail. Nothing was deployed. ==="
    python -c "import weasyprint" 2>&1 >/dev/null | tail -1
    echo "Fix: sudo apt install -y libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-dejavu-core"
    exit 1
fi

# Ensure swap exists — collectstatic OOMs on t2.micro without it
if [ ! -f /swapfile ]; then
    echo "=== Creating 1GB swap (one-time) ==="
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# Ensure the `postgres` OS user can reach the app dir — needed for WAL
# archive_command to run at all. /home/ubuntu is 750 by default, which
# silently blocks postgres (it's neither owner nor in the ubuntu group)
# from even traversing into it; discovered the hard way via a real Docker
# drill where archiving failed for hours with zero visible error until
# someone went looking. Execute-only on the home dir (not a broader chmod)
# so postgres can reach a path it already knows without being able to list
# anything else in there (.ssh/, shell history, etc). rwx on logs/ because
# django.setup() (needed to read R2 creds) also opens the app's log files.
if ! sudo -u postgres test -r "$APP_DIR/.env" 2>/dev/null; then
    echo "=== Granting postgres ACL access for WAL archiving (one-time) ==="
    sudo setfacl -m u:postgres:x /home/ubuntu
    sudo setfacl -R -m u:postgres:rwx $APP_DIR/logs
    sudo setfacl -R -d -m u:postgres:rwx $APP_DIR/logs
fi

# Ensure the backup/recovery cron jobs exist — idempotent via the marker
# comment below, so re-running deploy.sh (or rebuilding the server from
# scratch) never depends on someone remembering the manual runbook steps
# in docs/MEDIA_AND_BACKUPS.md. Does NOT touch postgresql.conf/archive_mode —
# that's a rare, restart-requiring change, deliberately kept a one-time
# manual step rather than something a routine deploy could ever re-trigger.
if ! crontab -l 2>/dev/null | grep -q "rasova-backups-managed"; then
    echo "=== Installing backup cron jobs (one-time) ==="
    (crontab -l 2>/dev/null; cat <<CRON
# rasova-backups-managed — deploy.sh checks for this comment, don't remove it
0 2 * * * cd $APP_DIR && .venv/bin/python scripts/backup/backup_to_r2.py >> $APP_DIR/logs/backup.log 2>&1
0 3 * * 0 cd $APP_DIR && .venv/bin/python scripts/backup/base_backup_to_r2.py >> $APP_DIR/logs/base_backup.log 2>&1
*/15 * * * * cd $APP_DIR && sudo -u postgres .venv/bin/python scripts/backup/check_wal_archiving_health.py >> $APP_DIR/logs/wal_health.log 2>&1
CRON
    ) | crontab -
fi

echo "=== Pulling latest code ==="
OLD_REV=$(git rev-parse HEAD 2>/dev/null || echo "none")
git fetch origin qsr
git reset --hard origin/qsr
NEW_REV=$(git rev-parse HEAD)
CHANGED=$(git diff --name-only "$OLD_REV" "$NEW_REV" 2>/dev/null || echo "ALL")

# Dependencies — only when requirements.txt actually changed (pip is slow on t3.micro)
if echo "$CHANGED" | grep -q "requirements.txt"; then
    echo "=== Dependencies (requirements changed) ==="
    pip install -r requirements.txt --quiet
else
    echo "=== Dependencies unchanged — skipping pip ==="
fi

# Beat as a systemd service (systemd/celery-beat.service). Installed whenever
# the server's copy is missing or differs from the repo's, so a new server
# gets it with no manual step and an edited unit always reaches the server.
# The restart section below starts it and stops any beat left over from the
# old nohup start.
if ! cmp -s "$APP_DIR/systemd/celery-beat.service" /etc/systemd/system/celery-beat.service; then
    echo "=== Installing the celery-beat service ==="
    sudo cp "$APP_DIR/systemd/celery-beat.service" /etc/systemd/system/celery-beat.service
    sudo systemctl daemon-reload
    sudo systemctl enable celery-beat
fi

echo "=== Migrate ==="
python manage.py migrate --noinput

# Static — only when static assets changed. collectstatic is the slowest step on
# a small box (manifest hashing 600+ files); skipping it keeps code-only deploys fast
# AND avoids the gunicorn-stop downtime.
if echo "$CHANGED" | grep -qE '(^| )static/|/static/|^public/|\.css$|\.js$|\.svg$'; then
    echo "=== Static changed — stopping gunicorn to free RAM, then collecting ==="
    sudo systemctl stop gunicorn 2>/dev/null || sudo fuser -k 8000/tcp 2>/dev/null || true
    sleep 1
    python manage.py collectstatic --noinput
else
    echo "=== Static unchanged — skipping collectstatic ==="
fi

echo "=== Restarting services ==="
mkdir -p $APP_DIR/logs

# gunicorn — try systemd, fall back to direct start
if ! sudo systemctl restart gunicorn 2>/dev/null; then
    pkill -f 'gunicorn.*core.wsgi' 2>/dev/null || true
    sleep 1
    setsid gunicorn --bind 127.0.0.1:8000 --workers 2 --threads 4 \
        --worker-class gthread --timeout 120 \
        --daemon core.wsgi:application
fi

# redis — best-effort
sudo systemctl restart redis 2>/dev/null || \
sudo systemctl start redis 2>/dev/null || true

# celery — try systemd, fall back to nohup
if ! sudo systemctl restart celery 2>/dev/null; then
    # Match the REAL command line ('celery -A core worker'); the old pattern
    # 'celery worker' never matched, so workers piled up on every deploy and
    # ate all the RAM. -9 to be sure the whole worker group dies.
    pkill -9 -f 'celery -A core worker' 2>/dev/null || true
    sleep 2
    setsid nohup celery -A core worker \
        --queues=default,printing --concurrency=2 --loglevel=warning \
        >> $APP_DIR/logs/celery.log 2>&1 &
fi

# celery beat — restarted on every deploy, or CELERY_BEAT_SCHEDULE changes
# (like the demo-tenant reset job) never take effect on the running process.
# Exactly one beat may run, or every scheduled job runs twice. It is its own
# service (systemd/celery-beat.service) so it starts on boot and after a
# crash; a beat left over from the old nohup start is stopped here.
if sudo systemctl restart celery-beat 2>/dev/null; then
    BEAT_PID=$(systemctl show -p MainPID --value celery-beat)
    for pid in $(pgrep -f 'celery -A core beat' || true); do
        if [ "$pid" != "$BEAT_PID" ]; then
            sudo kill "$pid" 2>/dev/null && echo "=== Stopped a second celery beat (pid $pid) ==="
        fi
    done
else
    echo "=== WARNING: no celery-beat service, starting beat with nohup. It will NOT come back after a reboot."
    echo "    Install it once: see the top of systemd/celery-beat.service ==="
    pkill -9 -f 'celery -A core beat' 2>/dev/null || true
    sleep 1
    setsid nohup celery -A core beat --loglevel=warning \
        --schedule=$APP_DIR/logs/celerybeat-schedule \
        >> $APP_DIR/logs/celery-beat.log 2>&1 &
fi
# A stopped beat takes a moment to exit, so count for up to 5 seconds.
for i in 1 2 3 4 5; do
    BEATS=$(pgrep -fc 'celery -A core beat' || true)
    [ "$BEATS" = "1" ] && break
    sleep 1
done
if [ "$BEATS" = "1" ]; then
    echo "=== celery beat: $(systemctl is-active celery-beat 2>/dev/null), 1 running ==="
else
    echo "=== WARNING: $BEATS celery beats running (must be exactly 1, or scheduled jobs run twice or not at all) ==="
fi

sleep 3
HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/)
echo "=== Deploy complete — HTTP $HTTP ==="

# Informational only — never fails the deploy over this. Surfaces a stalled
# WAL archiver (the exact failure mode that fills this box's disk silently)
# right in the deploy log instead of waiting for the next 15-min cron tick.
# Runs as postgres — it reads Postgres's own 700-owned data directory.
sudo -u postgres .venv/bin/python scripts/backup/check_wal_archiving_health.py || \
    echo "=== NOTE: WAL archiving health check reported an issue — see above ==="

exit 0
