#!/bin/bash
# Emergency restart — no deploy, just restarts all services
# Run this after a reboot or when the site goes down
# Usage: bash restart.sh

set -e
cd /home/ubuntu/rasova
source .venv/bin/activate

echo "=== Restarting Redis ==="
sudo systemctl start redis || true

echo "=== Restarting Gunicorn ==="
sudo systemctl restart gunicorn || {
    echo "systemctl not set up yet — starting manually"
    fuser -k 8000/tcp 2>/dev/null || true
    sleep 1
    gunicorn --bind 127.0.0.1:8000 --workers 2 --threads 4 --worker-class gthread --timeout 120 --daemon core.wsgi:application
}

echo "=== Restarting Celery ==="
sudo systemctl restart celery 2>/dev/null || {
    echo "systemctl not set up — starting manually"
    pkill -f 'celery worker' 2>/dev/null || true
    sleep 1
    setsid nohup celery -A core worker \
      --queues=default,printing --concurrency=2 --loglevel=warning \
      >> /home/ubuntu/rasova/logs/celery.log 2>&1 &
}

sleep 2
HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/)
echo "=== All services up — HTTP $HTTP ==="
