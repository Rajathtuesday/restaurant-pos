# Rasova - Restaurant Management Platform

> Cloud-based POS and restaurant management system for Indian restaurants.  
> Built with Django 6.0.3 · PostgreSQL · AWS · Multi-tenant SaaS architecture.

---

## What Is Rasova

Rasova is a full-stack restaurant management platform built for dine-in restaurants in India. It handles the complete lifecycle of a restaurant - from table management and kitchen order tickets to billing, inventory, shift management, and business reports.

**Key differentiator:** An AI-powered menu importer that reads a photograph of any menu - handwritten, printed, or PDF - and imports all items, categories, and prices in under 60 seconds. No manual data entry.

---

## Features

### Core POS
- **Floor plan** - Live color-coded table states (Free, Ordering, Preparing, Served, Billing, Cleaning)
- **Order management** - Multi-item orders with modifiers, notes, and real-time updates
- **KOT system** - Kitchen Order Tickets with concurrency-safe numbering (`select_for_update`)
- **Kitchen display** - Live kitchen screen with item status tracking
- **Waiter calls** - Customer QR-triggered waiter call with rate limiting (60-second dedup)
- **Table merge and transfer** - Combine tables or move orders between them

### Billing and Payments
- **Split billing** - Split order equally across N people
- **Partial payments** - Accept multiple payments against one order
- **Payment methods** - Cash, UPI, Card (configurable per outlet)
- **Discounts** - Percentage or flat discount at order or item level (manager/owner only)
- **Complimentary items** - Mark individual items as complimentary
- **Refunds** - Two-level refund approval (manager/owner role required)
- **Payment bypass** - Emergency close with daily limit enforcement and audit log
- **GST-compliant bills** - Per-item GST rates, CGST/SGST split, GSTIN and FSSAI on bill

### Inventory
- **Real-time deduction** - Inventory deducted on KOT send
- **Availability check** - Orders blocked if stock is insufficient
- **Recipe management** - Link ingredients to menu items
- **Low stock alerts** - Configurable threshold warnings

### Ordering
- **QR self-ordering** - Customer scans table QR → views menu → places order → appears in kitchen
- **AI menu importer** - Photograph any menu format → items imported via Gemini AI in ~60 seconds
- **Aggregator webhooks** - Zomato/Swiggy order ingestion with HMAC signature verification

### Shift Management
- **Cash sessions** - Open/close sessions with opening balance
- **Shift reconciliation** - Expected vs actual cash with discrepancy tracking
- **Shift reports** - Per-shift sales, payment breakdown, tips

### Reports (8 report types)
- Daily and hourly sales
- Item-wise and category-wise performance
- Kitchen and waiter performance
- Table utilization
- Inventory consumption
- Dashboard metrics (owner view)

### CRM
- Guest profiles and visit history
- Loyalty points
- Reservations

### Multi-tenancy and Access Control
- **Multi-tenant SaaS** - Each restaurant fully isolated by tenant + outlet
- **Subdomain routing** - `restaurantname.rasova.app` per tenant
- **Role-based access** - Owner, Manager, Waiter, Cashier, Chef, Agent
- **Agency dashboard** - Manage multiple restaurant clients

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Django 6.0.3 |
| Database | PostgreSQL 16 |
| Web server | Nginx + Gunicorn |
| Auth protection | django-axes (brute-force lockout) |
| Static files | WhiteNoise + S3 (optional) |
| Media storage | Local disk or AWS S3 |
| Error tracking | Sentry |
| AI | Google Gemini (menu import) |
| Printing | python-escpos (thermal printers) |
| Deployment | AWS EC2 + GitHub Actions CI/CD |

---

## Project Structure

```
rasova/
|-- accounts/          # User auth, roles, login, dashboard
|-- agency/            # Multi-client agency management
|-- core/              # Settings, middleware, decorators, URLs
|-- crm/               # Guest profiles, loyalty, reservations
|-- inventory/         # Stock, recipes, deduction
|-- menu/              # Categories, items, modifiers, GST, digital menu
|-- notifications/     # In-app notification system
|-- orders/            # Core POS — orders, KOT, billing, payments
|   |-- management/    # 9 management commands (stress test, seed, audit)
|   |-- services/      # 14 service modules (payment, refund, KOT, void...)
|   |-- tests/         # Financial flow, critical path, pos flow tests
|   |-- utils/         # Payment validation, order utilities
|   └-- views/         # Billing, order, table, waiter, kitchen views
|-- reports/           # 8 report services + dashboard metrics
|-- setup/             # Payment config, kitchen stations, aggregator config
|-- shifts/            # Cash sessions, shift management, reconciliation
|--tenants/           # Tenant and outlet models
└-- static/            # CSS, JS, images
```

---

## Local Development Setup

### Prerequisites

- Python 3.12+
- PostgreSQL 16
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/rasova.git
cd rasova

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create environment file
cp .env.example .env
# Edit .env with your values — see Environment Variables section
```

### Database Setup

```bash
# Create PostgreSQL database
psql -U postgres
CREATE DATABASE rasova_db;
CREATE USER rasova_user WITH PASSWORD 'yourpassword';
GRANT ALL PRIVILEGES ON DATABASE rasova_db TO rasova_user;
\q
```

### Run

```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py collectstatic --noinput
python manage.py runserver
```

Visit `http://localhost:8000`

---

## Environment Variables

Create a `.env` file in the project root:

```env
# Django Core
DEBUG=False
SECRET_KEY=your-50-char-random-secret-key-here
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com,YOUR_SERVER_IP

# Database
DB_ENGINE=django.db.backends.postgresql
DB_NAME=rasova_db
DB_USER=rasova_user
DB_PASSWORD=your-db-password
DB_HOST=localhost
DB_PORT=5432

# App URL (used for QR codes)
BASE_URL=https://yourdomain.com

# Error Tracking (optional)
SENTRY_DSN=https://your-key@sentry.io/project-id

# AI Menu Import (required for AI importer feature)
GEMINI_API_KEY=your-gemini-api-key

# AWS S3 Media Storage (optional — uses local disk if not set)
# AWS_STORAGE_BUCKET_NAME=your-bucket-name
# AWS_S3_REGION_NAME=ap-south-1
# AWS_ACCESS_KEY_ID=your-key
# AWS_SECRET_ACCESS_KEY=your-secret

# Aggregator Webhooks
# AGGREGATOR_IP_ALLOWLIST=127.0.0.1

# Security
AXES_FAILURE_LIMIT=5

# WhatsApp Notifications (optional)
# INTERAKT_API_KEY=your-key
```

---

## Production Deployment (AWS EC2)

### Server Requirements

- Ubuntu 24.04 LTS
- t3.micro (demo/small) or t3.small (1–5 restaurants)
- 20GB EBS storage

### Quick Deploy

```bash
# On your server — run once to set up
git clone https://github.com/YOUR_USERNAME/rasova.git ~/restaurant-pos
cd ~/restaurant-pos
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env  # fill in your values
python manage.py migrate
python manage.py collectstatic --noinput
```

Then set up Gunicorn as a systemd service and Nginx as reverse proxy — see the deployment guide in `/docs/deployment.md`.

### CI/CD — GitHub Actions

Every push to `main` automatically:
1. Runs the full test suite against a real PostgreSQL instance
2. If tests pass — SSHs into the server and runs `deploy.sh`
3. If tests fail — server is never touched

**Setup:** Add these secrets to your GitHub repo (Settings → Secrets → Actions):

| Secret | Value |
|--------|-------|
| `SERVER_HOST` | Your server IP |
| `SERVER_USER` | `rasova` |
| `SERVER_SSH_KEY` | Your deploy private key |
| `SERVER_PORT` | `22` |

---

## Management Commands

```bash
# Seed a restaurant with test data
python manage.py seed_restaurant

# Run the full POS flow test
python manage.py test_pos_flow

# Stress test concurrency (simulates rush hour)
python manage.py simulate_restaurant_rush

# Audit the POS for data integrity issues
python manage.py audit_pos

# Reset POS data (dev only)
python manage.py reset_pos
```

---

## Running Tests

```bash
# Run all tests
python manage.py test accounts orders tenants inventory reports crm shifts --verbosity=2

# Run specific test module
python manage.py test orders.tests.test_financial_flows
python manage.py test orders.tests.test_critical
python manage.py test orders.tests.test_pos_flow

# Check for missing migrations
python manage.py migrate --check
```

---

## Architecture Notes

### Multi-Tenancy

Every database query is scoped by `tenant + outlet`. The `TenantMiddleware` resolves tenant from subdomain in production and from `?tenant=slug` in development (DEBUG only). The `@tenant_required` decorator enforces isolation on every view.

### Financial Integrity

All payment operations use `select_for_update()` to prevent race conditions. Refund rows are excluded from payment validation using `.exclude(method="refund")`. The `validate_order_payment()` function uses `TOLERANCE = Decimal("0.00")` — no rounding buffer.

### KOT Numbering

`DailyKOTCounter` uses `select_for_update()` to guarantee unique sequential KOT numbers even under concurrent load from multiple tablets.

### Concurrency

Service layer (`orders/services/`) handles all business logic with proper `transaction.atomic()` scoping. Views are thin — they validate input and call services.

---

## Security

- **Brute-force protection** — django-axes, 5 attempts before lockout, 1-hour cooldown
- **Multi-tenant isolation** — every query scoped by tenant + outlet, cross-tenant access raises `PermissionDenied`
- **Role-based access** — `@role_required` decorator on all sensitive endpoints
- **HMAC webhook verification** — aggregator (Zomato/Swiggy) webhooks verified with `hmac.compare_digest`
- **CSRF protection** — Django's built-in CSRF middleware, `CSRF_TRUSTED_ORIGINS` configured
- **Error tracking** — Sentry configured for production exceptions
- **Structured logging** — Thread-local tenant/outlet context injected into every log record

---

## Roadmap

- [ ] Razorpay UPI payment gateway integration
- [ ] QR digital menu redesign (photo cards, cart, filters)
- [ ] PDF bill download
- [ ] Day-end Z-report
- [ ] WhatsApp bill delivery (Interakt)
- [ ] Offline mode (Level 1 — Service Worker)
- [ ] Hotel module (room management, housekeeping, folio billing)
- [ ] Southeast Asia expansion (multi-currency, localization)

---

## License

Proprietary. All rights reserved.  
© 2026 Rasova. Built in Bengaluru.

---

*Built solo. Refined continuously.*
