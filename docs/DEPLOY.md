# Rasova POS — Complete Deployment Runbook
### EC2 · Ubuntu · Nginx · Gunicorn · PostgreSQL · WhiteNoise · CI/CD
*Every command is copy-pasteable. Follow in order. Never skip a step.*

---

## PART 1 — AWS EC2: First-time setup

### 1.1 Launch the instance
1. Go to AWS Console → EC2 → **Launch Instance**
2. Name: `rasova-prod`
3. OS: **Ubuntu 24.04 LTS** (free tier eligible)
4. Instance type: `t3.small` (2 vCPU, 2 GB RAM — minimum for prod)
5. Key pair: create one called `rasova` → download `rasova.pem` → store safely
6. Security Group — open these ports:
   - **22** (SSH) — your IP only
   - **80** (HTTP) — 0.0.0.0/0
   - **443** (HTTPS) — 0.0.0.0/0
7. Storage: 20 GB gp3
8. Click **Launch Instance**

### 1.2 Allocate an Elastic IP (so IP never changes on restart)
1. EC2 → **Elastic IPs** → **Allocate Elastic IP Address** → Allocate
2. Select the new IP → **Actions** → **Associate Elastic IP**
3. Instance: select your rasova instance → Associate
4. **Write down the IP** — this is permanent: `_______________`

### 1.3 Connect to the server
**Option A — Instance Connect (no .pem needed):**
1. EC2 → Instances → select rasova → **Connect** → **EC2 Instance Connect** → Connect

**Option B — SSH from terminal:**
```bash
chmod 400 rasova.pem
ssh -i rasova.pem ubuntu@YOUR_ELASTIC_IP
```

---

## PART 2 — Server: System dependencies

### 2.1 Update system packages
```bash
sudo apt update && sudo apt upgrade -y
```

### 2.2 Install Python, pip, nginx, PostgreSQL, Redis
```bash
sudo apt install -y python3 python3-pip python3-venv \
    nginx \
    postgresql postgresql-contrib \
    redis-server \
    git \
    build-essential \
    libpq-dev
```

### 2.3 Verify everything installed
```bash
python3 --version      # should be 3.10+
nginx -v               # should show nginx/1.x
psql --version         # should show 14+
redis-cli ping         # should return PONG
```

---

## PART 3 — PostgreSQL: Create database and user

### 3.1 Open PostgreSQL shell
```bash
sudo -u postgres psql
```

### 3.2 Run these SQL commands (inside psql)
```sql
CREATE DATABASE rasova_prod;
CREATE USER rasova WITH PASSWORD 'PICK_A_STRONG_PASSWORD';
ALTER ROLE rasova SET client_encoding TO 'utf8';
ALTER ROLE rasova SET default_transaction_isolation TO 'read committed';
ALTER ROLE rasova SET timezone TO 'Asia/Kolkata';
GRANT ALL PRIVILEGES ON DATABASE rasova_prod TO rasova;
\q
```

### 3.3 Write down your DB credentials
```
DB_NAME=rasova_prod
DB_USER=rasova
DB_PASSWORD=PICK_A_STRONG_PASSWORD
DB_HOST=127.0.0.1
DB_PORT=5432
```

---

## PART 4 — Clone the repo and create Python environment

### 4.1 Clone the repo
```bash
cd /home/ubuntu
git clone https://github.com/Rajathtuesday/restaurant-pos.git rasova
cd rasova
```

### 4.2 Create and activate virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4.3 Install Python dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## PART 5 — Environment variables (.env file)

### 5.1 Create the .env file
```bash
nano /home/ubuntu/rasova/.env
```

### 5.2 Paste and fill in ALL values
```env
SECRET_KEY=GENERATE_50_RANDOM_CHARS_HERE
DEBUG=False
ALLOWED_HOSTS=rasova.net,www.rasova.net,YOUR_ELASTIC_IP

DB_ENGINE=django.db.backends.postgresql
DB_NAME=rasova_prod
DB_USER=rasova
DB_PASSWORD=PICK_A_STRONG_PASSWORD
DB_HOST=127.0.0.1
DB_PORT=5432

REDIS_URL=redis://127.0.0.1:6379/0

BASE_URL=https://rasova.net
```

> **Generate SECRET_KEY:**
> ```bash
> python3 -c "import secrets; print(secrets.token_urlsafe(50))"
> ```

### 5.3 Load .env automatically on activate
```bash
echo 'set -a; source /home/ubuntu/rasova/.env; set +a' >> /home/ubuntu/rasova/.venv/bin/activate
source .venv/bin/activate
```

---

## PART 6 — Django: migrate and collect static

### 6.1 Run database migrations
```bash
cd /home/ubuntu/rasova
source .venv/bin/activate
python manage.py migrate
```

### 6.2 Collect static files (WhiteNoise needs this)
```bash
python manage.py collectstatic --noinput
```

### 6.3 Create a superuser (first time only)
```bash
python manage.py createsuperuser
```

### 6.4 Run Django system check
```bash
python manage.py check --deploy
```
> Fix any WARNINGS before going live. ERRORs are blockers.

---

## PART 7 — Gunicorn: run Django in production

### 7.1 Test gunicorn works (foreground first)
```bash
cd /home/ubuntu/rasova
source .venv/bin/activate
gunicorn --bind 127.0.0.1:8000 --workers 2 core.wsgi:application
```
> Visit `http://YOUR_ELASTIC_IP` — if nginx is set up you should see something.
> Press **Ctrl+C** to stop.

### 7.2 Run gunicorn as a daemon (background)
```bash
gunicorn \
  --bind 127.0.0.1:8000 \
  --workers 2 \
  --timeout 120 \
  --daemon \
  core.wsgi:application
```

### 7.3 Check it is running
```bash
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/
# Should return 200
```

### 7.4 Stop and restart gunicorn (for future deploys)
```bash
sudo fuser -k 8000/tcp 2>/dev/null || true
sleep 2
gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 120 --daemon core.wsgi:application
```

---

## PART 8 — Nginx: reverse proxy

### 8.1 Create the nginx site config
```bash
sudo tee /etc/nginx/sites-available/rasova << 'EOF'
server {
    listen 80;
    server_name rasova.net www.rasova.net YOUR_ELASTIC_IP;

    client_max_body_size 20M;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }
}
EOF
```
> Replace `YOUR_ELASTIC_IP` with the actual IP.

### 8.2 Enable the site
```bash
sudo ln -sf /etc/nginx/sites-available/rasova /etc/nginx/sites-enabled/rasova
```

### 8.3 Disable the default nginx site (prevents conflicts)
```bash
sudo rm -f /etc/nginx/sites-enabled/default
```

### 8.4 Test and reload nginx
```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 8.5 Enable nginx on boot
```bash
sudo systemctl enable nginx
```

---

## PART 9 — DNS: point your domain to EC2

### 9.1 Go to your domain registrar (GoDaddy / Namecheap / etc.)

### 9.2 Add these DNS records
| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | @ | YOUR_ELASTIC_IP | 300 |
| A | www | YOUR_ELASTIC_IP | 300 |

### 9.3 Wait for propagation (5–30 minutes)
```bash
# Check from server if DNS has propagated
nslookup rasova.net
# Should show your Elastic IP
```

---

## PART 10 — HTTPS: SSL certificate with Certbot

> Do this ONLY after DNS has propagated (Step 9.3).

### 10.1 Install Certbot
```bash
sudo apt install -y certbot python3-certbot-nginx
```

### 10.2 Get SSL certificate
```bash
sudo certbot --nginx -d rasova.net -d www.rasova.net
```
> Follow prompts. Enter email. Agree to terms.
> When asked about redirect: choose **2 (Redirect)** — forces HTTPS.

### 10.3 Verify auto-renewal works
```bash
sudo certbot renew --dry-run
```

### 10.4 Check new nginx config (certbot modifies it)
```bash
cat /etc/nginx/sites-available/rasova
```

---

## PART 11 — GitHub Actions CI/CD

### 11.1 Go to GitHub repo settings
`github.com/Rajathtuesday/restaurant-pos` → **Settings** → **Secrets and variables** → **Actions**

### 11.2 Add these 3 secrets
| Name | Value |
|------|-------|
| `EC2_HOST` | Your Elastic IP (e.g. `18.60.238.104`) |
| `EC2_USER` | `ubuntu` |
| `EC2_KEY` | Contents of `rasova.pem` — paste the FULL file including `-----BEGIN RSA PRIVATE KEY-----` |

### 11.3 How to get EC2_KEY content
```bash
# On your LOCAL machine (where rasova.pem is stored)
cat rasova.pem
# Copy everything including the header and footer lines
```

### 11.4 Test the pipeline
1. Make any small commit and push to `qsr` branch
2. Go to **Actions** tab → watch the run
3. Should show: test ✓ → deploy ✓

### 11.5 What CI/CD does automatically on every push to qsr
```
1. Runs full test suite (228 tests) against PostgreSQL + Redis
2. If tests pass → SSH into EC2
3. git reset --hard origin/qsr
4. pip install -r requirements.txt
5. python manage.py migrate
6. python manage.py collectstatic
7. Kill old gunicorn → start new gunicorn
8. Smoke test: curl /health/ → log 200
```

---

## PART 12 — Maintenance and common operations

### 12.1 Deploy manually (if CI/CD is down)
```bash
ssh -i rasova.pem ubuntu@YOUR_ELASTIC_IP
cd /home/ubuntu/rasova
git fetch origin qsr && git reset --hard origin/qsr
source .venv/bin/activate
pip install -r requirements.txt --quiet
python manage.py migrate
python manage.py collectstatic --noinput
sudo fuser -k 8000/tcp 2>/dev/null || true
sleep 2
gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 120 --daemon core.wsgi:application
sleep 2
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8000/health/
```

### 12.2 View live error logs
```bash
tail -f /home/ubuntu/rasova/logs/errors.log
```

### 12.3 Check what's running on port 8000
```bash
sudo fuser -n tcp 8000
# or
ps aux | grep gunicorn
```

### 12.4 Restart only nginx (no downtime)
```bash
sudo systemctl reload nginx
```

### 12.5 Full restart (brief downtime)
```bash
sudo systemctl restart nginx
sudo fuser -k 8000/tcp 2>/dev/null || true
sleep 2
source /home/ubuntu/rasova/.venv/bin/activate
cd /home/ubuntu/rasova
gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 120 --daemon core.wsgi:application
```

### 12.6 Connect to PostgreSQL directly
```bash
psql -U rasova -d rasova_prod -h 127.0.0.1
```

### 12.7 Run a Django management command
```bash
cd /home/ubuntu/rasova
source .venv/bin/activate
python manage.py shell   # interactive Python shell with Django loaded
python manage.py dbshell # PostgreSQL shell with app DB
```

---

## PART 13 — What to do when IP changes (EC2 stop/start)

> This only happens if you did NOT set up an Elastic IP. If you did Part 1.2 correctly, skip this.

### 13.1 Find the new IP
AWS Console → EC2 → Instances → your instance → **Public IPv4 address**

### 13.2 Update GitHub secret
Settings → Secrets → **EC2_HOST** → Update → new IP

### 13.3 Update nginx on the server (via Instance Connect)
```bash
sudo sed -i 's/OLD_IP/NEW_IP/g' /etc/nginx/sites-available/rasova
sudo nginx -t && sudo systemctl reload nginx
```

### 13.4 Update Django ALLOWED_HOSTS (edit .env)
```bash
nano /home/ubuntu/rasova/.env
# Update ALLOWED_HOSTS to include new IP
```
Then restart gunicorn (see 12.1).

---

## PART 14 — Common errors and fixes

### ❌ `dial tcp X.X.X.X:22: i/o timeout` (CI/CD deploy fails)
**Cause:** EC2_HOST secret has wrong IP, or EC2 instance is stopped.
**Fix:**
1. Check instance is RUNNING in AWS Console
2. Update EC2_HOST secret with current IP
3. Re-run the failed GitHub Actions job

### ❌ `ALLOWED_HOSTS` error / 400 Bad Request
**Cause:** `ALLOWED_HOSTS` in `.env` doesn't include the domain/IP being used.
**Fix:**
```bash
nano /home/ubuntu/rasova/.env
# Add the domain: ALLOWED_HOSTS=rasova.net,www.rasova.net,18.60.238.104
```
Restart gunicorn after saving.

### ❌ `No open cash session` / payment 400
**Cause:** QSR tenant needs a CashSession but none exists.
**Fix:** Already handled in code — auto-creates CashSession for QSR/café tenants.
If still failing, check `orders/views/payment_views.py`.

### ❌ Login page 500 error
**Cause:** Redis not running + `@ratelimit` decorator fails.
**Fix:**
```bash
sudo systemctl start redis-server
sudo systemctl enable redis-server
```
The app also has `RATELIMIT_FAIL_OPEN = True` as fallback.

### ❌ Static files not loading (CSS/JS broken after deploy)
**Cause:** `collectstatic` not run, or nginx serving wrong path.
**Fix:**
```bash
python manage.py collectstatic --noinput
```
WhiteNoise serves static files through gunicorn — no nginx `/static/` alias needed.

### ❌ 404 on landing page `rasova.net/`
**Cause:** WhiteNoise not finding `public/index.html`.
**Fix:**
```bash
ls /home/ubuntu/rasova/public/index.html  # verify file exists
python manage.py collectstatic --noinput  # re-run collectstatic
sudo fuser -k 8000/tcp 2>/dev/null || true && sleep 2
gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 120 --daemon core.wsgi:application
```

### ❌ 502 Bad Gateway
**Cause:** Gunicorn is not running.
**Fix:**
```bash
cd /home/ubuntu/rasova
source .venv/bin/activate
gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 120 --daemon core.wsgi:application
```

### ❌ Database migration fails
**Cause:** DB credentials wrong or PostgreSQL not running.
**Fix:**
```bash
sudo systemctl start postgresql
sudo systemctl status postgresql
# Then re-run:
python manage.py migrate
```

---

## PART 15 — Quick reference card

```
App directory:    /home/ubuntu/rasova/
Venv:             /home/ubuntu/rasova/.venv/
Environment:      /home/ubuntu/rasova/.env
Nginx config:     /etc/nginx/sites-available/rasova
Gunicorn port:    127.0.0.1:8000
DB name:          rasova_prod
DB user:          rasova
Redis:            redis://127.0.0.1:6379/0
Logs:             /home/ubuntu/rasova/logs/errors.log
Landing page:     /home/ubuntu/rasova/public/index.html
Static files:     /home/ubuntu/rasova/staticfiles/ (after collectstatic)
Branch to deploy: qsr
GitHub repo:      github.com/Rajathtuesday/restaurant-pos
Domain:           rasova.net
```

---

## PART 16 — Checklist before going live with a new client

- [ ] Tenant created in Django admin
- [ ] Owner account created and linked to tenant
- [ ] Outlet created with correct address, GSTIN, FSSAI
- [ ] Menu imported (AI import or manual)
- [ ] Payment methods configured (Cash / UPI / Card)
- [ ] GST pricing mode set (Inclusive for QSR, Exclusive for fine dining)
- [ ] Kitchen station created (if using thermal printer — enter printer IP)
- [ ] Test order placed → KOT printed → payment processed → receipt printed
- [ ] Staff accounts created (Manager, Cashier, Waiter as needed)
- [ ] Reports dashboard checked — correct data showing
- [ ] Client given login URL and credentials

---

*Rasova POS · Branch: `qsr` · Founder: Rajath · fortunecloudmentors@gmail.com*
