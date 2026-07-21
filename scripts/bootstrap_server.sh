#!/usr/bin/env bash
#
# Rasova POS — one-shot bootstrap for a FRESH Ubuntu/Debian server.
#
# Installs everything and brings the app up: system packages, PostgreSQL,
# Redis, nginx, the Python venv, the .env (with freshly generated secrets),
# migrations, static files, and the systemd services. Idempotent-ish: safe to
# re-run; it skips things that already exist.
#
# Requires: Ubuntu 24.04+ (Django 6.0 needs Python 3.12+). Run as root:
#
#     sudo DOMAIN=rasova.net GOOGLE_API_KEY=AIza... bash scripts/bootstrap_server.sh
#
# You can also just run `sudo bash scripts/bootstrap_server.sh` and edit the
# CONFIG block below first. Anything left blank gets a sensible default.
#
set -euo pipefail

# ─────────────────────────── CONFIG (override via env) ───────────────────────────
APP_USER="${APP_USER:-ubuntu}"
APP_DIR="${APP_DIR:-/home/$APP_USER/rasova}"
REPO_URL="${REPO_URL:-https://github.com/Rajathtuesday/restaurant-pos.git}"
BRANCH="${BRANCH:-qsr}"

DB_NAME="${DB_NAME:-pos_db}"
DB_USER="${DB_USER:-pos_user}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -hex 16)}"   # generated once if not given

DOMAIN="${DOMAIN:-}"                 # e.g. rasova.net  (blank = localhost only, DEBUG stays off)
GOOGLE_API_KEY="${GOOGLE_API_KEY:-}" # optional — enables AI menu import
# ──────────────────────────────────────────────────────────────────────────────

log() { echo -e "\n\033[1;36m== $* ==\033[0m"; }

[ "$(id -u)" -eq 0 ] || { echo "Please run as root (sudo)."; exit 1; }
id "$APP_USER" >/dev/null 2>&1 || { echo "User '$APP_USER' does not exist. Create it or set APP_USER."; exit 1; }

# ── 1. System packages ──────────────────────────────────────────────────────────
log "1/9  System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
    python3 python3-venv python3-dev build-essential git curl openssl \
    postgresql postgresql-contrib libpq-dev \
    redis-server \
    nginx \
    libjpeg-dev zlib1g-dev libffi-dev \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libcairo2 \
    shared-mime-info fonts-dejavu

# Django 6.0 needs Python 3.12+
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)'; then
    echo "WARNING: system python3 is < 3.12; Django 6.0 will not run. Use Ubuntu 24.04+."
fi

# ── 2. PostgreSQL: role + database ──────────────────────────────────────────────
log "2/9  PostgreSQL database + user"
systemctl enable --now postgresql
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET client_encoding TO 'utf8';"
sudo -u postgres psql -c "ALTER ROLE $DB_USER SET timezone TO 'UTC';"

# ── 3. Redis (Celery broker + cache) ────────────────────────────────────────────
log "3/9  Redis"
systemctl enable --now redis-server

# ── 4. Clone / update the repo ──────────────────────────────────────────────────
log "4/9  Application code ($BRANCH)"
if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git clone -b "$BRANCH" "$REPO_URL" "$APP_DIR"
else
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin "$BRANCH"
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard "origin/$BRANCH"
fi

# ── 5. Python venv + dependencies ───────────────────────────────────────────────
log "5/9  Python venv + dependencies"
[ -d "$APP_DIR/.venv" ] || sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# ── 6. .env (generate secrets on first run) ─────────────────────────────────────
log "6/9  Environment file"
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    SECRET=$(sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -c \
        "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
    FERNET=$(sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -c \
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

    HOSTS="localhost,127.0.0.1"
    BASEURL="http://localhost:8000"
    if [ -n "$DOMAIN" ]; then
        HOSTS="$DOMAIN,www.$DOMAIN,.$DOMAIN,localhost,127.0.0.1"
        BASEURL="https://$DOMAIN"
    fi

    cat > "$ENV_FILE" <<EOF
SECRET_KEY=$SECRET
FIELD_ENCRYPTION_KEY=$FERNET
DEBUG=False
ALLOWED_HOSTS=$HOSTS
BASE_URL=$BASEURL

DB_ENGINE=django.db.backends.postgresql
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_HOST=localhost
DB_PORT=5432
DB_CONN_MAX_AGE=60

REDIS_URL=redis://127.0.0.1:6379/0
GOOGLE_API_KEY=$GOOGLE_API_KEY
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
EOF
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "  wrote $ENV_FILE  (generated SECRET_KEY + FIELD_ENCRYPTION_KEY; DB password inside)"
else
    echo "  $ENV_FILE already exists — leaving it untouched"
fi

# ── 7. Migrate + collect static ─────────────────────────────────────────────────
log "7/9  Migrate + collectstatic"
run_dj() { sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && '$APP_DIR/.venv/bin/python' manage.py $*"; }
run_dj migrate --noinput
run_dj collectstatic --noinput

# ── 8. systemd services (gunicorn + celery) ─────────────────────────────────────
log "8/9  systemd services"
for svc in gunicorn celery; do
    # Rewrite the shipped unit's hardcoded /home/ubuntu/rasova + ubuntu user to
    # whatever APP_DIR/APP_USER this install used.
    sed -e "s#/home/ubuntu/rasova#$APP_DIR#g" \
        -e "s#^User=ubuntu#User=$APP_USER#" \
        -e "s#^Group=ubuntu#Group=$APP_USER#" \
        "$APP_DIR/systemd/$svc.service" > "/etc/systemd/system/$svc.service"
done
systemctl daemon-reload
systemctl enable --now gunicorn celery

# ── 9. nginx reverse proxy ──────────────────────────────────────────────────────
# Proxies EVERYTHING to gunicorn, including /static/ — WhiteNoise (running
# inside the Django app) serves static files with proper cache headers and
# manifest-hashed filenames. No nginx alias for /static/ or /media/: this
# used to alias both directly to disk, which drifted from what's actually
# running in production and, for /media/, is flat-out stale — uploaded media
# lives on Cloudflare R2 now (MEDIA_URL points at R2 directly whenever
# AWS_STORAGE_BUCKET_NAME is set), so nothing ever requests /media/ on the
# app's own domain. Confirmed by diffing this against the live server's
# actual /etc/nginx/sites-available/rasova before fixing it here.
log "9/9  nginx"
cat > /etc/nginx/sites-available/rasova <<EOF
server {
    listen 80;
    server_name ${DOMAIN:-_};
    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/rasova /etc/nginx/sites-enabled/rasova
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# ── swap (collectstatic OOMs on t2/t3.micro without it) ─────────────────────────
if [ ! -f /swapfile ]; then
    log "Creating 1GB swap"
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ── Done ────────────────────────────────────────────────────────────────────────
HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health/ || echo "000")
cat <<EOF

=========================  RASOVA IS UP  =========================
  App dir   : $APP_DIR
  Database  : $DB_NAME (user $DB_USER)
  Env file  : $ENV_FILE   (chmod 600)
  Services  : systemctl status gunicorn celery nginx
  Health    : HTTP $HTTP  from  http://127.0.0.1:8000/health/

  NEXT STEPS
  1. Create the first admin:
       sudo -u $APP_USER $APP_DIR/.venv/bin/python $APP_DIR/manage.py createsuperuser
  2. If you didn't pass GOOGLE_API_KEY, add it to $ENV_FILE for AI menu import,
     then:   sudo systemctl restart gunicorn celery
  3. For HTTPS: sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx -d $DOMAIN
==================================================================

EOF
