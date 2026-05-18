#!/bin/bash
# Rasova EC2 Deploy Script
# Runs on the server when triggered by GitHub Actions or manually
set -e

PROJECT_DIR="/home/ubuntu/rasova"
VENV="$PROJECT_DIR/.venv/bin"
BRANCH="qsr"

echo "=== Rasova Deploy $(date) ==="
cd "$PROJECT_DIR"

echo "1. Pull latest code..."
git fetch origin
git reset --hard origin/$BRANCH

echo "2. Install dependencies..."
$VENV/pip install -r requirements.txt --quiet

echo "3. Run migrations..."
$VENV/python manage.py migrate --run-syncdb

echo "4. Collect static files..."
$VENV/python manage.py collectstatic --noinput --clear

echo "5. Restart gunicorn..."
sudo fuser -k 8000/tcp 2>/dev/null || true
sleep 2
$VENV/gunicorn \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 120 \
    --daemon \
    --access-logfile "$PROJECT_DIR/logs/access.log" \
    --error-logfile "$PROJECT_DIR/logs/gunicorn_error.log" \
    core.wsgi:application

sleep 2
echo "6. Verify server is running..."
curl -s -o /dev/null -w "HTTP %{http_code}" http://localhost:8000/health/ && echo " OK"

echo "=== Deploy complete ==="
