# Rasova POS — Compressed Context Prompt
### Paste this at the start of any new Claude session to restore full context

---

## Project
**Rasova POS** — cloud-based multi-tenant restaurant POS for India.
- Repo: `github.com/Rajathtuesday/restaurant-pos` branch `qsr`
- Live: `rasova.net` (landing) · `spice.rasova.net` (demo tenant)
- Server: AWS EC2 t3.micro · Ubuntu · `18.60.238.104` · ap-south-2 Hyderabad
- Founder: Rajath · `fortunecloudmentors@gmail.com` · Bengaluru
- Pricing: ₹999/month QSR single outlet · first client (Spice Garden) gets 1 free month

## Stack
Django 6.x · PostgreSQL 16 · Redis 7 · Celery · WhiteNoise · gunicorn · nginx · GitHub Actions CI/CD

## Tenant hierarchy
`Tenant → Outlet → User` (role: owner/manager/cashier/waiter/chef)
Tenant types: `fine_dining` / `franchise` / `cafe`
Features are per-tenant-type, overridable via `TenantFeatureOverride`

## Multi-tenancy / subdomains
- `TenantMiddleware` reads subdomain from host → resolves to `Tenant` by `slug`
- Each tenant gets `slug.rasova.net` auto-generated from name (always lowercase)
- `@tenant_required` decorator enforces cross-tenant isolation
- Login at `rasova.net/login` → after auth redirects to `slug.rasova.net/dashboard/`
- Reserved slugs: `www api app admin superadmin static media support login logout signup register help mail smtp rasova health favicon billing dashboard setup`
- Dev mode: no subdomains, use `?tenant=slug` or `/demo/` switcher (DEBUG only, 404 in prod)

## Key models
- `Tenant` — slug, tenant_type, logo, subscription_fee, subscription_status
- `Outlet` — address, phone, gst_no, fssai_no, sac_code, gst_inclusive, print_mode
- `KitchenStation` — name, is_default, printer_ip, printer_port, paper_width_mm, cut_type, printer_encoding
- `KOTBatch` — groups items by station, one per station per order-send
- `Order / OrderItem` — recalculate_totals() handles GST inclusive/exclusive
- `DailyTokenCounter` — locked with select_for_update, prevents duplicate tokens

## Printing architecture
**Cloud can't reach local printer** (NAT). Two modes:
1. **Browser print** — `thermal_receipt.html` opens in popup, `window.print()`, uses OS printer. Works with USB. With Chrome `--kiosk-printing` flag = zero-click automatic.
2. **ESC/POS** — `python-escpos` over TCP:9100. Only works when printer is on SAME NETWORK as Django server (localhost dev or local Rasova install).

**Print modes detected automatically in `print_bill_task`:**
- QSR + no station printers → Token + KOTs as connected strip (FULL cut at end)
- QSR + station printers → Token receipt only (KOTs already printed at stations)
- Fine dining + station printers → Bill only
- Hotel/one cashier printer → Bill → PARTIAL → KOT1 → PARTIAL → KOT2 → FULL CUT

**`scripts/virtual_printer.py`** — run locally for ESC/POS testing, listens on `127.0.0.1:9100`

**For cloud-hosted Rasova (spice.rasova.net) + USB printer at restaurant:**
- Leave printer IP empty in Kitchen Stations
- Create Chrome shortcut with `--kiosk-printing --app=https://spice.rasova.net`
- Browser popup opens after payment → prints via USB to Windows default printer
- KOT sections appear at bottom of the receipt

## CI/CD
GitHub Actions: tests (PostgreSQL + Redis) → deploy via SSH on push to `qsr`
Deploy runs: git reset → pip install → migrate → **collectstatic** → restart gunicorn
Secrets needed: `EC2_HOST=18.60.238.104` · `EC2_USER=ubuntu` · `EC2_KEY=pem contents`
**collectstatic must run in both test step AND deploy step** (CompressedManifestStaticFilesStorage requires manifest)

## Static files
WhiteNoise serves static through gunicorn. `WHITENOISE_ROOT = BASE_DIR / 'public'` serves `public/index.html` at `/` (landing page). `WHITENOISE_INDEX_FILE = True`.
nginx proxies everything to gunicorn — no `/static/` alias in nginx.

## Landing page
`public/index.html` served by WhiteNoise at `/`. Pure HTML, no Django template engine. Authenticated users hitting `/` are redirected to `/dashboard/`. nginx has no special location block for `/`.

## Known issues / open items
- Payment gateway (Razorpay/Pine Labs) — not built, deal blocker
- Offline mode — not built (browser print survives 60s drops, full offline needs 3-4 weeks)
- Local print agent — not built (needed for cloud ESC/POS to local printer)
- `rasova.net` DNS → Cloudflare wildcard `*.rasova.net` → EC2 (needs to be set up if not done)
- Elastic IP allocated but verify it's associated with current instance

## Recently fixed bugs (don't re-break)
- `timezone.localdate()` in kitchen report tests (not `timezone.now().date()` — IST timezone mismatch after 18:30 UTC)
- `running_order_items` API returns all order statuses now (not just open/billing)
- `bill.html` payment success now tries ESC/POS then falls back to browser popup
- `set_default_station` is a POST-only endpoint — template uses `<form>` not `<a href>`
- `update_printer_config` reads JSON body (not `request.POST` — apiClient sends JSON)
- `log-bypass` URL is at `/log-bypass/<id>/` not `/orders/log-bypass/<id>/`
- Inventory board uses `html.dark` (not `body.dark`) and `rasova_theme` localStorage key

## Demo setup for Spice Garden
1. `spice.rasova.net` — cafe type, slug `spice`
2. Chrome shortcut: `"C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk-printing --app=https://spice.rasova.net`
3. BillTouch ZY306 USB → set as Windows default printer
4. Kitchen Stations → General station → leave printer IP empty (browser print fallback)
5. Payment → receipt auto-prints to BillTouch with no click

## Dev commands
```bash
# Start dev server
cd f:\pos && .venv\scripts\activate && python manage.py runserver

# Virtual printer (ESC/POS testing)
python scripts/virtual_printer.py

# Demo tenant switcher
http://localhost:8000/demo/

# Manual deploy on server
cd /home/ubuntu/rasova && git pull origin qsr
source .venv/bin/activate
python manage.py migrate && python manage.py collectstatic --noinput
sudo fuser -k 8000/tcp 2>/dev/null || true && sleep 2
gunicorn --bind 127.0.0.1:8000 --workers 2 --timeout 120 --daemon core.wsgi:application
```
