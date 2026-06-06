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
git fetch origin qsr
git reset --hard origin/qsr

echo "=== Dependencies ==="
pip install -r requirements.txt --quiet

echo "=== Migrate ==="
python manage.py migrate

echo "=== Stopping gunicorn before collectstatic (free RAM) ==="
sudo systemctl stop gunicorn 2>/dev/null || sudo fuser -k 8000/tcp 2>/dev/null || true
sleep 1

echo "=== Static files ==="
python manage.py collectstatic --noinput

echo "=== Restarting services ==="
sudo systemctl restart gunicorn
sudo systemctl restart redis  2>/dev/null || sudo systemctl start redis
sudo systemctl restart celery 2>/dev/null || true

sleep 3
HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/)
echo "=== Deploy complete — HTTP $HTTP ==="
exit 0
