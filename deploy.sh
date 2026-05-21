#!/bin/bash
# Rasova production deploy script
set -e

APP_DIR=/home/ubuntu/rasova
cd $APP_DIR
source .venv/bin/activate

echo "=== Pulling latest code ==="
git fetch origin qsr
git reset --hard origin/qsr

echo "=== Dependencies ==="
pip install -r requirements.txt --quiet

echo "=== Migrate ==="
python manage.py migrate

echo "=== Static files ==="
python manage.py collectstatic --noinput

echo "=== Restarting gunicorn ==="
sudo fuser -k 8000/tcp 2>/dev/null || true
sleep 2
gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 120 --daemon core.wsgi:application

echo "=== Restarting Celery ==="
pkill -f 'celery worker' 2>/dev/null || true
sleep 1
setsid nohup celery -A core worker \
  --queues=default,printing --concurrency=2 --loglevel=warning \
  >> /home/ubuntu/rasova/logs/celery.log 2>&1 &

sleep 3
HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/)
echo "=== Deploy complete — HTTP $HTTP ==="
exit 0
