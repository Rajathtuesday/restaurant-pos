# Rasova - Restaurant POS Platform

> Cloud-based POS and restaurant management system for Indian restaurants.  
> Django 6.0 · PostgreSQL · Celery + Redis · Multi-tenant SaaS · ESC/POS thermal printing.

---

## What Is Rasova

Rasova is a full-stack restaurant management platform built for Indian restaurants - fine dining, QSR counters, and cafés. It handles the complete order lifecycle: table management, kitchen tickets, billing, thermal printing, inventory, reports, and an order history with a full audit trail.

Two things that make it different from existing Indian POS software:

1. **AI menu import** - photograph any printed or handwritten menu → Gemini AI imports all items, categories, and prices in under 60 seconds. No manual data entry.
2. **Design** - built to look good. The billing screen, kitchen display, and QR menu are all significantly cleaner than Petpooja or POSist.

---

## Restaurant Types Supported

| Type | Key features |
|---|---|
| **Fine Dining** | Floor plan, table merge/transfer, waiter calls, kitchen display, split bill |
| **QSR / Fast Food** | Token counter, single-printer strip (bill + KOTs on one slip), pay-first flow |
| **Café** | Mix of both - floor plan + token system |

Each type gets its own default feature set. Owners and superusers can override individual features per outlet.

---

## Features

### Core POS
- Live floor plan with colour-coded table states (Free → Ordering → Preparing → Served → Billing → Cleaning)
- Section grouping on floor map with urgency highlighting (orange > 15min, red > 30min)
- Multi-item orders with modifiers, notes, and item-level discounts
- KOT (Kitchen Order Ticket) system with concurrency-safe numbering (`select_for_update`)
- Live kitchen display - item status tracking (Preparing → Ready → Served → Bumped)
- Table merge, unmerge, and order transfer between tables
- Waiter call via QR - rate-limited to one call per 60 seconds per table
- Kitchen messages to waiter - "Delayed 15 mins", custom messages, scoped per waiter

### QSR Counter Mode
- Token ordering - sequential daily tokens with automatic assignment
- Pay-first flow - cashier collects payment, then slip prints
- **QSR strip printing** - bill + all KOTs print as one connected strip (partial cuts between, final full cut)
- Auto-KOT at payment - for no-KDS setups, KOTs are created at payment time
- Auto-reset after payment - screen clears for next customer after 2.5 seconds

### Thermal Printing
- **Browser-based** - `window.print()` via OS print dialog, works with any printer that has a Windows driver. Zero local installation.
- **ESC/POS network** - direct TCP to printer at port 9100. Works via local Celery worker on the same LAN.
- **Strip mode** - QSR: receipt → partial cut → KOT 1 → partial cut → KOT N → full cut
- **Split mode** - Fine dining: bill = full cut (customer copy), KOTs = partial cuts (kitchen chain)
- SAC code (996331) printed on every bill - GST compliance
- GSTIN, FSSAI, address, and phone printed on header
- `python manage.py preview_print <order_id>` - see exact output without a printer

### Billing and Payments
- Split billing - multiple payment methods on one order
- Partial payments - collect in stages
- Payment methods - Cash, UPI, Card (configurable per outlet), Razorpay UPI QR (dynamic, webhook-confirmed)
- Offline cash payments - a bill can be closed and paid in cash with no internet connection; queues locally and syncs automatically once reconnected
- Discounts - percentage or flat, at order or item level (manager/owner only)
- Complimentary items - mark individual items as ₹0
- Refunds - two-level approval (manager/owner required)
- GST-compliant bills - per-item GST rates, CGST/SGST split, GSTIN, FSSAI, SAC code
- Thermal receipt page - `/thermal-receipt/<id>/` auto-prints via browser

### Order History
- Searchable, filterable list of all past orders - `/history/`
- Filters: date range, status, payment method, source, staff, free-text search
- Role-scoped: owner = all orders, cashier = 30 days, waiter = today only
- Slide-in detail panel: items (including voided with reasons), payments, audit trail
- Full audit trail - who voided what, who applied discounts, when order was paid
- CSV export (owner/manager only, max 2,000 rows per export)
- Handles: deleted menu items, split payments, refunds, complimentary orders, QR orders

### Inventory
- Real-time stock deduction on KOT send
- Recipe management - link ingredients to menu items
- Low stock alerts with configurable thresholds
- Purchase orders

### Ordering
- QR self-ordering - customer scans → views menu → places order → appears for staff approval
- AI menu importer - photograph any menu format → imported via Gemini AI
- Aggregator webhooks - Zomato/Swiggy order ingestion with HMAC signature verification and idempotency

### Reports
- Daily and hourly sales
- Item and category performance
- Kitchen and waiter performance
- Payment method breakdown
- Dashboard metrics (owner view with live auto-refresh)

### Multi-tenancy and Access Control
- Every DB query scoped to `tenant + outlet` - zero cross-restaurant data leakage
- Feature flags per tenant type (fine dining / QSR / café) with per-outlet overrides
- Role-based access: Owner, Manager, Cashier, Waiter, Chef
- **Superuser control panel** - `/superuser/` - create restaurants, configure printers, apply feature presets, manage staff. No Django admin needed for setup.
- 24 configurable feature flags - toggle per restaurant without code changes

### Background Tasks (Celery + Redis)
- Thermal printing is async - payment response returns in ~12ms, printer runs in background
- Task idempotency - Redis key prevents double-printing on task retry
- Worker isolation - `RASOVA_TENANT_ID` + `RASOVA_OUTLET_ID` env vars scope each local worker to one restaurant
- Graceful fallback - if Redis is unreachable, falls back to synchronous print

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django 6.0.3 |
| Database | PostgreSQL 16 |
| Task queue | Celery 5.6 + Redis 7 |
| Web server | Nginx + Gunicorn |
| Auth protection | django-axes (brute-force lockout) |
| Static files | WhiteNoise |
| Error tracking | Sentry |
| Printing | python-escpos (ESC/POS thermal) + browser `window.print()` |
| Deployment | GitHub Actions CI/CD |

---

## Project Structure

```
f:\pos\
├── accounts/           User auth, roles, login, dashboard, superuser panel
│   └── views/          auth_views, dashboard_views, feature_views, superuser_views
├── agency/             Multi-client agency management
├── core/               Settings, middleware, decorators, Celery app, features
├── crm/                Guest profiles, loyalty, reservations
├── inventory/          Stock, recipes, deduction, purchase orders
├── menu/               Categories, items, modifiers, GST, QR digital menu
│   └── views/          customer, management, item, category, modifier, gst, ai
├── notifications/      In-app notification system
├── orders/             Core POS - orders, KOT, billing, payments, history
│   ├── management/     Management commands (preview_print, seed, audit, stress test)
│   ├── services/       14 service modules (payment, KOT, printing, void, refund…)
│   ├── tasks.py        Celery tasks - print_kot_task, print_bill_task
│   ├── tests/          246 tests across financial, security, API, concurrency
│   └── views/          billing_core, payment, discount, print, kitchen, table, history
├── reports/            8 report services + dashboard metrics
├── setup/              Kitchen stations, payment config, onboarding wizard
│   └── views/          core, promo, onboarding, aggregator
├── shifts/             Cash sessions, shift management, reconciliation
├── tenants/            Tenant and outlet models (includes SAC code field)
└── templates/
    └── core/base.html  Master layout - notification poller, dark mode, theme
```

---

## Local Development

### Prerequisites

- Python 3.12+
- PostgreSQL 16
- Redis (for Celery) - `docker run -d -p 6379:6379 redis:alpine`
- Git

### Installation

```bash
git clone https://github.com/Rajathtuesday/restaurant-pos.git rasova
cd rasova

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env   # edit with your values
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

### Run Everything (3 terminals)

```bash
# Terminal 1 - Django
python manage.py runserver 0.0.0.0:8000

# Terminal 2 - Celery worker (background printing)
celery -A core worker --loglevel=info -Q printing,default

# Terminal 3 - Redis (if not running as a service)
docker run -d -p 6379:6379 redis:alpine
```

Visit `http://localhost:8000` → log in as superuser → go to `/superuser/` to create your first restaurant.

### Preview Printing Without a Printer

```bash
python manage.py preview_print --list          # show recent orders
python manage.py preview_print <order_id>      # fine-dining mode
python manage.py preview_print <id> --strip    # QSR strip mode
python manage.py preview_print <id> --width 32 # 58mm paper
```

---

## Environment Variables

```env
# Django
SECRET_KEY=your-50-char-secret
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,YOUR_SERVER_IP
BASE_URL=https://yourdomain.com

# Database
DB_ENGINE=django.db.backends.postgresql
DB_NAME=rasova_db
DB_USER=rasova_user
DB_PASSWORD=your-password
DB_HOST=localhost
DB_PORT=5432

# Redis + Celery
REDIS_URL=redis://127.0.0.1:6379/0

# Local Celery worker isolation (set on the device in the restaurant)
RASOVA_TENANT_ID=1    # only process jobs for this tenant
RASOVA_OUTLET_ID=1    # only process jobs for this outlet

# Error tracking
SENTRY_DSN=https://your-key@sentry.io/project-id

# AI menu import
GEMINI_API_KEY=your-gemini-api-key

# WhatsApp bills (optional)
# META_WHATSAPP_TOKEN=your-token
# META_WHATSAPP_PHONE_ID=your-phone-id
```

---

## Thermal Printing Architecture

Rasova supports two printing modes:

**Browser printing** (zero local installation):
```
Browser → window.print() → OS print dialog → select printer → prints
Requires: printer driver installed on the cashier's computer
Works with: USB or LAN printers that have Windows/Mac drivers
```

**ESC/POS via local Celery worker** (full control):
```
Cloud Django → Redis → Local Celery worker → TCP:9100 → Printer
Requires: one always-on device on the restaurant LAN (Raspberry Pi, spare laptop)
Works with: any ESC/POS network printer (LAN port required)
```

For QSR with a single counter printer - both bill and KOTs print together as one strip, customer carries it to the food counter.

---

## Management Commands

```bash
# Printing
python manage.py preview_print --list
python manage.py preview_print <order_id> --strip --width 40

# Data and testing
python manage.py seed_restaurant          # seed test data
python manage.py test_pos_flow            # run the full POS flow
python manage.py simulate_restaurant_rush # stress test concurrency
python manage.py audit_pos                # check data integrity
python manage.py reset_pos                # clear POS data (dev only)
```

---

## Tests

**792+ tests across the codebase. All passing.**

```bash
python manage.py test --keepdb                           # all tests
python manage.py test orders.tests.test_critical         # critical paths
python manage.py test orders.tests.test_financial_flows  # payment flows
python manage.py test accounts tenants setup menu        # individual apps
```

Test coverage includes:
- Financial accuracy (Decimal math, GST rounding, no float bugs)
- Tenant isolation (cross-restaurant data access blocked)
- Role-based access (waiter cannot pay, cashier cannot discount)
- Concurrency (double-payment race condition, KOT number uniqueness under load)
- Celery task idempotency (print job cannot fire twice)
- API endpoints (notification, kitchen data, tables data)
- Feature flag logic (defaults + overrides per tenant type)

---

## Architecture Notes

### Multi-Tenancy
Every DB query is scoped by `tenant + outlet`. `TenantMiddleware` resolves the tenant from the logged-in user. `@tenant_required` enforces isolation on every view. A bug cannot accidentally expose one restaurant's data to another.

### Financial Integrity
All payment operations use `select_for_update()` to prevent race conditions. `Decimal` arithmetic throughout - no float math on money. Refund rows excluded from payment validation. KOT numbers use `select_for_update()` on `DailyKOTCounter` to guarantee uniqueness under concurrent load.

### Printing Isolation
Each local Celery worker reads `RASOVA_TENANT_ID` and `RASOVA_OUTLET_ID` from environment. Tasks for other restaurants are silently skipped. Task idempotency keys in Redis prevent double-printing on retry. Tasks expire after 30 minutes - old print jobs are never processed.

### Service Layer
Business logic lives in `orders/services/` - 14 service modules, none of which know about HTTP. Views are thin: validate input → call service → return response.

---

## Security

- **Brute-force protection** - django-axes, 5 failed attempts → 1-hour lockout, correctly scoped per real visitor IP (see below)
- **Real client IP resolution behind Cloudflare + Nginx** - `core.utils.get_client_ip()`, checks `CF-Connecting-IP` then `X-Forwarded-For`, wired into both axes and rate limiting. Without this, every visitor to the server shared one IP bucket.
- **Tenant isolation** - every query scoped, cross-tenant access raises 403
- **Role-based access** - `@role_required` decorator on all sensitive endpoints
- **Feature gating** - `@feature_required` - disabled features return JSON 403 (not HTML) for API calls
- **`@tenant_required` superuser bypass** - superusers (`tenant=None` by design) no longer get locked out of views stacked with this decorator
- **HMAC webhook verification** - Zomato/Swiggy webhooks verified with `hmac.compare_digest`
- **CSRF** - Django middleware + `CSRF_TRUSTED_ORIGINS` configured, cookie renamed (`csrftoken2`) to eliminate stale-duplicate-cookie collisions after a domain-scope change, and a custom `CSRF_FAILURE_VIEW` (`core.views.csrf_failure`) returns a friendly reload page for real navigation or clean JSON for fetch/apiClient calls, instead of Django's bare default 403
- **QR ordering is token-only** - `digital_menu()` used to also accept a plain `?table=<id>`, letting anyone enumerate table ids and receive that table's real secret `qr_token` with no scan required. Removed outright after confirming it had zero real callers.
- **Rate limiting** - `django-ratelimit` on public QR ordering endpoint (20 req/min per IP) and login (10/min)
- **Error tracking** - Sentry for production exceptions
- **Structured logging** - tenant/outlet context injected into every log record, including a dedicated `pos.security` / `logs/security.log` channel for CSRF and axes events

---

## Roadmap

**Done:**
- [x] Multi-tenant architecture with feature flags
- [x] Fine dining floor plan + QSR token counter
- [x] ESC/POS thermal printing + browser printing
- [x] QSR strip printing (bill + KOTs as one slip)
- [x] Celery + Redis async printing with idempotency
- [x] Superuser control panel for restaurant setup
- [x] Order history with audit trail and CSV export
- [x] SAC code (GST compliance) on all bills
- [x] AI menu import (Gemini)
- [x] 792+ passing tests (financial, security, concurrency, business-date accuracy)
- [x] GitHub Actions CI/CD
- [x] Razorpay UPI QR - dynamic QR on the bill screen, auto-confirms via webhook, configured per outlet in Payment Methods setup
- [x] Offline write queue - orders placed during connectivity loss sync when back online (IndexedDB, `offlineQueue` in `templates/core/base.html`); extended to cash payment closure too (`offlinePaymentQueue`) - a bill can now be closed and paid in cash with no connection, and syncs once back online. UPI/card intentionally excluded - both require a live gateway round-trip to actually verify payment, which no client-side queue can fake without accepting an unconfirmed claim as real.
- [x] Real unit conversion across production capacity, COGS, inventory restore, and QSR deduction - previously four separate, silently-drifting implementations
- [x] Business-day-accurate reporting - every report (Z-report, owner dashboard, sales/item/category/table/kitchen/waiter breakdowns, inventory usage/wastage/cost, the tax-inspection view, CSV/Excel exports) now uses the outlet's actual business-day cutoff instead of a plain calendar date. A restaurant open past midnight no longer loses an entire evening's revenue from "today's" numbers.
- [x] Self-service staff account management - owner/manager can reset a locked-out staff member's password or deactivate/reactivate an account directly from the Staff page, no server access required. Deactivation force-ends any already-open session and preserves all historical shift/cash-session/order records rather than deleting them.
- [x] Mobile-responsive setup pages

**Next:**
- [ ] GSTR-1 export - accountant-ready GST return CSV
- [ ] Celery task monitoring - see pending and failed print jobs in UI
- [ ] Subscription billing - auto-charge restaurants monthly fee
- [ ] WhatsApp bill delivery
- [ ] External security audit
- [ ] Full local-first offline deployment (running detached from the cloud for extended outages, not just brief drops) - a substantially larger undertaking than the write queue above, only worth it for restaurants with sustained multi-hour/day outages rather than occasional drops

---

## License

Proprietary. All rights reserved.  
© 2026 Rasova. Built in Bengaluru.

---

*Built solo. Shipped in months. Refined every day.*
