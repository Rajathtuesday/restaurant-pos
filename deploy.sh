#!/bin/bash
# Rasova production deploy script
set -e

APP_DIR=/home/ubuntu/rasova
cd $APP_DIR
source .venv/bin/activate

# Ensure swap exists — collectstatic OOMs on t2.micro without it
if [ ! -f /swapfile ]; then
    echo "=== Creating 1GB swap (one-time) ==="
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
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

sleep 3
HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/)
echo "=== Deploy complete — HTTP $HTTP ==="
exit 0
