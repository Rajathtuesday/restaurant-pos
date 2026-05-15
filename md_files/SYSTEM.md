# Rasova POS — System Reference

A multi-tenant restaurant POS built with Django. Supports fine-dining restaurants, franchises (QSR), and cafés from a single codebase. Every restaurant ("tenant") gets its own isolated data, feature set, and user accounts.

---

## 1. High-Level Architecture

```
Browser / PWA
     │
     ▼
Django (WSGI / Gunicorn)
  ├── Middleware chain
  │     TenantMiddleware → ContextLoggingMiddleware → RequestLoggingMiddleware → Auth
  ├── Apps:
  │     accounts  tenants  menu  orders  kitchen  billing
  │     reports   inventory  crm  shifts  setup  notifications
  ├── PostgreSQL  (primary data store)
  ├── Redis / Django cache  (printer errors, metrics, feature overrides)
  └── Celery (background tasks: KOT printing, WhatsApp receipts)
```

---

## 2. Multi-Tenancy

Every piece of data is scoped to a **Tenant** (the restaurant company) and usually an **Outlet** (a branch).

### Tenant types

| Type | Description | Default features |
|---|---|---|
| `fine_dining` | Full-service table restaurant | Floor plan, KOT, QR menu, CRM, inventory |
| `franchise` | QSR / fast food counter | Token system, simple billing, barcode transfer |
| `cafe` | Coffee shop / counter | Token system, QR menu, KOT |

### How tenants are identified per request

`TenantMiddleware` runs on every request:

1. Reads the **subdomain** from the `Host` header (`myrestaurant.rasova.in` → slug `myrestaurant`).
2. Falls back to `?tenant=<slug>` query param or `session["dev_tenant_slug"]` in `DEBUG` mode (for local dev without a real subdomain).
3. Sets `request.tenant` for downstream use.

The decorator `@tenant_required` (in `core/decorators.py`) blocks access if `request.tenant` is `None`.

---

## 3. Feature System

Features gate UI and API access. Resolution order per request:

1. **`TenantFeatureOverride`** DB rows (explicit per-tenant on/off, set by superusers via `/settings/features/`).
2. **`TENANT_FEATURES[tenant.tenant_type]`** in `core/features.py` (the type default).

```python
from core.features import has_feature
if has_feature(request.user.tenant, "split_bill"):
    ...
```

Overrides are **cached on the tenant object** (`tenant._feature_overrides`) for the lifetime of the request to avoid N+1 DB hits. The cache is deleted after any toggle.

The `@feature_required("feature_name")` view decorator returns 403 if the feature is off for that tenant.

### Full feature list (by group)

**Ordering & Billing:** `floor_plan`, `token_system`, `simple_billing`, `direct_billing_mode`, `split_bill`, `running_order`, `merge_tables`, `qr_menu`, `modifiers`, `platform_sync`

**Kitchen:** `kot_system`, `kitchen_display`, `multi_kitchen`, `waiter_call`

**Inventory:** `inventory`, `barcode_transfer`, `purchase_orders`

**Reports:** `reports`, `gstr_export`, `advanced_reports`

**CRM & Loyalty:** `crm`, `reservations`, `loyalty_points`

**Menu:** `ai_menu_import`

---

## 4. User Roles & Login Routing

Users extend Django's `AbstractUser` with two extra fields: `tenant` (FK) and `outlet` (FK).

| Role | Lands on | Notes |
|---|---|---|
| `owner` | `/dashboard/` | Sees all outlets |
| `manager` | `/dashboard/` | Single outlet |
| `cashier` | `/dashboard/` (direct_billing_mode) or `/token-dashboard/` | QSR only; fine-dining → `/billing/` |
| `waiter` | `/tables/` (fine-dining) or `/token-dashboard/` (QSR) | |
| `chef` | `/kitchen/` | |
| `agent` | `/sales/` | Superuser sales team |

Login routing lives in `accounts/views.py → login_view`.

---

## 5. Core Data Models

### Tenant & Outlet (`tenants/models.py`)

```
Tenant ──< Outlet
  slug (unique, auto-generated from name)
  tenant_type: fine_dining | franchise | cafe
  subscription_status: trial | active | suspended
```

### User (`accounts/models.py`)

```
User (AbstractUser)
  tenant → Tenant (FK, nullable for superusers)
  outlet → Outlet (FK)
  role: owner | manager | cashier | waiter | chef | agent
```

### Table (`orders/models.py`)

```
Table
  tenant, outlet
  name, section
  qr_token (UUID, unique — powers the digital QR menu)
  state: free | ordering | preparing | ready | billing | cleaning
```

### Order

```
Order
  tenant, outlet, table (nullable for QSR counter orders)
  status: open | billing | paid | closed | cancelled
  source: dine_in | takeaway | counter | zomato | swiggy | uber_eats | web
  order_number: INV-{outlet_id}-{YYYYMMDD}-{NNNN}  (auto-generated, sequential per outlet per day)
  subtotal, gst_total, discount_total, grand_total, round_off
```

Order numbers are generated inside a `select_for_update()` lock on `DailyOrderCounter` to guarantee no gaps or duplicates under concurrent load.

### OrderItem

```
OrderItem → Order
  menu_item, quantity, price, gst_percentage, total_price
  item_discount_pct (per-item discount)
  status: review | pending | sent | preparing | ready | served | voided
  is_complimentary, is_takeaway
  kot → KOTBatch (which print batch this item belongs to)
```

### KOTBatch (Kitchen Order Ticket)

```
KOTBatch → Order
  kot_number (sequential per order)
  station → KitchenStation (optional routing to grill, cold, bar, etc.)
  status: confirmed | preparing | ready
```

### Payment

```
Payment → Order
  method: cash | upi | card | zomato | swiggy | uber_eats | web | refund
  amount, reference, paid_at
```

### TokenOrder (QSR only)

```
TokenOrder → Order (OneToOne)
  token_number (daily sequential, locked via DailyTokenCounter row)
  date
  is_online: False = walk-in "#5", True = aggregator "O-5"
```

### Promo

```
Promo → Tenant (outlet optional = tenant-wide)
  discount_type: percentage | amount
  discount_value, min_order_value
  max_uses, usage_count (exhaustion tracked with select_for_update)
  valid_from, valid_until
```

---

## 6. Order Lifecycle — Fine Dining

```
Waiter opens /billing/?table=<id>
        │
        ▼
  Table state: ordering
        │
  Items added to cart → POST /orders/add-item/
        │
  KOT sent → POST /orders/send-kot/
        │        Background task prints to thermal printer
        │        Items move to status: sent → preparing → ready
        ▼
  Table state: preparing / ready
        │
  "Generate Bill" → POST /orders/generate-bill/<order_id>/
        │        Order status: billing  |  Table state: billing
        ▼
  Bill screen /bill/<order_id>/
        │
  Payment entered → POST /orders/pay/
        │        Order status: paid  |  Table state: cleaning
        ▼
  Waiter marks clean → POST /tables/clean/<table_id>/
        │        Table state: free
```

### Table merging

A `TableMerge` record links secondary tables to a primary. Secondary tables show state `merged` on the floor map. Orders are placed on the primary table only. Unmerge is a single POST call.

### Table transfer

An order can move from one table to another (must be free) via `POST /tables/transfer/`. The source table returns to `free`, the destination table inherits the order.

---

## 7. Order Lifecycle — QSR / Token Mode

```
Cashier opens /token-dashboard/
        │
  "New Order" → creates Order + TokenOrder (token counter row locked)
        │        Token displayed as #5 (walk-in) or O-5 (online)
        ▼
  Items added → KOT printed → kitchen prepares
        │
  Token called → cashier opens billing → payment taken
        │
  "Next Order" shortcut returns directly to billing screen for speed
```

### `direct_billing_mode` feature

When enabled, clicking "Core Ops" on the owner dashboard creates a token in the background and redirects straight to `/billing/` — skipping the token dashboard entirely. Used at high-volume single-cashier counters.

---

## 8. KOT Printing

Printing is handled in `orders/services/printing_service.py` and triggered as a background thread from `orders/tasks.py`.

**ESC/POS format features:**
- Items prefixed with `[V]` (veg) or `[N]` (non-veg) flags sourced from `MenuItem.is_veg`.
- Multi-kitchen routing: items go to the station assigned on the `MenuItem`, default to the outlet's main printer.
- KOT number is sequential per day per outlet (`DailyKOTCounter` with `select_for_update`).

**Printer failure handling:**
- On failure: `cache.set(f"printer_err_{outlet_id}", {station, kot, detail}, TTL=180s)`.
- Frontend polls `/orders/printer-status/` every 20 seconds.
- A red banner appears in the billing screen with a plain-language message: _"Kitchen printer is not responding (KOT #12) — check the cable/network."_
- On next successful print: cache key is deleted, banner disappears.

**BUMP button (Kitchen Display):**
`POST /orders/bump-kot/<kot_id>/` marks all `sent`/`preparing` items on that KOT as `ready` in one tap. The button is visible on each KOT card in `/kitchen/`.

---

## 9. Billing Screen (`/billing/`)

Key behaviours:

- **Veg/non-veg dots** — each cart row shows a green dot (veg) or red dot (non-veg) pulled from `MenuItem.is_veg`. Passed through `openItemModal(id, name, price, gst, isVeg)` → cart object → `renderCart`.
- **Discounts** — order-level (% or flat ₹) and per-item percentage, both computed in `Order.recalculate_totals()`.
- **Promos** — staff can pick an active promo code; usage is tracked with a `select_for_update` race guard.
- **Split bill** — divide payment across multiple people/methods (feature-gated).
- **Offline banner** — `navigator.onLine` events show/hide an "Offline" pill. The Service Worker caches `/billing/` so the page loads even without network.
- **Customer name/phone** — fields exist on the `Order` model but are intentionally hidden from the billing UI (removed per UX decision).

---

## 10. Bill Screen (`/bill/<order_id>/`)

- **GST-compliant layout** — CGST + SGST line items grouped by rate, sourced from `Order.gst_breakdown`.
- **UPI QR** — generates a UPI deep link QR for the outstanding amount.
- **Change display** — when the entered cash amount exceeds the balance, a red box with the change amount appears in 2.8rem font for cashier at-a-glance reading.
- **WhatsApp receipt** — sends the bill URL to the customer's phone via WhatsApp link.
- **GSTR-1 export** — CSV download if `gstr_export` feature is enabled.

---

## 11. Floor Map (`/tables/`)

Live-refreshes every 5 seconds via `GET /tables-data/` (JSON). The `refreshLayout()` JS function rebuilds the entire grid on each tick.

### What each card shows

- **Status colour bar** at the top of the card (green=free, amber=ordering, orange=preparing, blue=served, cyan=billing, pink=cleaning, purple=merged, red=needs_approval).
- **Waiter badge** — initials circle in the top-right corner; red if order came via QR.
- **Timer** — minutes since order opened. Colour changes: amber >15 min, red >30 min.
- **Card border pulse** — `card-warn` (amber) >15 min, `card-urgent` (red, animated pulse) >30 min.
- **Cooking badge** — orange flame badge showing how many items are still `sent`/`preparing` in kitchen.
- **Section headers** — tables grouped by `Table.section` field with gold dividers spanning the full grid width.

### Manager alert strip

At the top of the grid, a status bar shows:
- `stuck >30m` chip (red) — count of active tables past the 30-minute mark.
- `need approval` chip (amber) — count of tables with QR items awaiting waiter approval.
- `awaiting payment` chip (cyan) — count of tables in billing state.
- Turns green with "All clear" when stuck + approval counts are both zero.

---

## 12. Kitchen Display (`/kitchen/`)

- Live-refreshes via `GET /kitchen-data/` (JSON).
- Table number shown at 2rem bold.
- Timer shown at 1.6rem bold.
- **BUMP button** — full-width, 56px tall (thumb-friendly). Calls `POST /orders/bump-kot/<id>/` and removes the card from the KDS.
- Item status flow: `sent` → `preparing` (chef taps) → `ready` (BUMP).

---

## 13. Owner Dashboard (`/dashboard/`)

### Metrics (live, 60-second refresh)

Metrics are computed by `reports/services/dashboard_metrics.py` and cached for 60 seconds per outlet per day. The AJAX endpoint is `GET /dashboard/metrics.json`.

| Metric | Source |
|---|---|
| Revenue | `Payment` records today, excluding refunds |
| Orders | `Order` records with status `closed` or `paid` today |
| Avg order value | Revenue ÷ Orders |
| Active tables | `Table` rows in `ordering/preparing/ready` |
| Kitchen orders | `Order` rows with at least one item in `sent/preparing` |
| Low stock | `InventoryItem` rows at or below `low_stock_threshold` |
| Voids today | `Order` rows cancelled today |
| Discounts | Sum and count of orders with `discount_total > 0` today |

### Mobile revenue strip

On mobile (< 768px), a gold revenue strip appears above all other content with the day's revenue in 2.4rem serif font, plus colour-coded pills for orders, tables, kitchen queue, voids, discounts, and low stock. Hidden on desktop via `@media (min-width: 768px) { display: none !important; }`.

---

## 14. Feature Flags UI (`/settings/features/`)

Superuser-only. Lets Rasova staff enable/disable any feature per tenant without code changes.

- Grouped by category (Ordering, Kitchen, Inventory, etc.).
- Each feature shows its current state: **on (default)**, **on (override)**, **off (default)**, **off (override)**.
- Toggle calls `POST /settings/features/toggle/` (AJAX, JSON body: `{tenant_id, feature, enabled}`).
- When toggled back to the type default, the override row is deleted (clean DB).
- After any toggle, `tenant._feature_overrides` is deleted from the instance cache so `has_feature()` re-reads from DB on the next call.

---

## 15. Logging

Four rotating log files under `logs/`:

| File | Handler | Level | Contains |
|---|---|---|---|
| `logs/pos.log` | `pos_file` | DEBUG | All app activity |
| `logs/errors.log` | `error_file` | WARNING | Warnings + errors across all loggers |
| `logs/django.log` | `django_file` | INFO | Django internals |
| `logs/security.log` | `security_file` | DEBUG | Auth events, security middleware |

**Logger namespaces:** `pos`, `pos.orders`, `pos.billing`, `pos.menu`, `pos.kitchen`, `pos.reports`, `pos.auth`, `pos.core`, `pos.inventory`, `pos.notifications`, `pos.shifts`, `pos.crm`, `pos.setup`.

**`RequestLoggingMiddleware`** logs every non-static request:
```
GET /billing/ 200  42ms  user=alice  tenant=The Grand Cafe
POST /orders/add-item/ 201  18ms  user=alice  tenant=The Grand Cafe
```
4xx/5xx responses log at WARNING level.

---

## 16. Middleware Chain

```
TenantMiddleware           — identifies tenant from subdomain / query param
ContextLoggingMiddleware   — captures outlet_id for structured log context
RequestLoggingMiddleware   — logs every request with timing and user info
SessionMiddleware
AuthenticationMiddleware
...
```

---

## 17. PWA / Offline Mode

`/sw.js` is served by `core/views.py → serve_sw()` (reads from `static/sw.js`, sets `Service-Worker-Allowed: /` header so the SW scope covers the whole app).

**Cache version:** `rasova-v3`

**Cached offline pages:** `/kitchen/` and `/billing/`

**Strategy:** Network-first for all requests. On network failure, if the path starts with `/billing/` or `/kitchen/`, the SW returns the cached version so staff can still take orders. All other offline requests get a generic offline page.

---

## 18. QR Menu & Customer Self-Ordering

Each `Table` has a unique `qr_token` (UUID). The QR code URL is:
```
/menu/digital-menu/?table_token=<uuid>
```

Customers scan → see the menu → add items → items land in the order with status `review`. A red `needs_approval` badge appears on the floor map. The waiter taps "Approve QR" → all items move to `sent` and a KOT is printed.

---

## 19. Notifications

`WaiterCall` — created when a customer scans their table QR and taps "Call Waiter". Resolved by the waiter from `/waiter-dashboard/`.

`KitchenMessage` — sent from the billing screen to the kitchen. Displayed on the KDS.

`Notification` model — general-purpose alerts (low stock, failed prints, etc.). Polled by the bell icon in the header.

---

## 20. Reports

`reports/services/dashboard_metrics.py` — today's metrics per outlet (cached 60s).

`/reports/dashboard/` — sales summary, item performance, shift reports, CSV export.

`gstr_export` feature — GSTR-1 compatible CSV with HSN codes, tax rates, invoice numbers.

`advanced_reports` feature — hourly trends, category deep-dives, custom date ranges.

---

## 21. Inventory

`InventoryItem` — stock level, unit, low stock threshold per outlet. Auto-deducted on KOT confirmation when `inventory` feature is enabled.

`barcode_transfer` — franchise feature: pre-made items moved from central kitchen to branch using a barcode scanner.

`purchase_orders` — raise POs to vendors, receive stock directly into inventory.

---

## 22. CRM

`crm` feature unlocks guest profiles linked to phone numbers. Visit history, total spend, and loyalty points (if `loyalty_points` is enabled) are tracked per guest.

`reservations` feature adds a booking calendar: date, time, party size, table pre-assignment.

---

## 23. Shifts

`shifts` app tracks open/close times per staff member per outlet. Shift reports show who was on duty during each sale.

---

## 24. Key URL Map

| URL | View | Role required |
|---|---|---|
| `/` | Redirect → login or dashboard | — |
| `/login/` | `login_view` | — |
| `/dashboard/` | `owner_dashboard` | owner, manager, cashier |
| `/dashboard/metrics.json` | `dashboard_metrics_json` | owner, manager, cashier |
| `/tables/` | `table_dashboard` | waiter, manager, owner |
| `/tables-data/` | `tables_data` (JSON) | same |
| `/billing/` | `billing_view` | cashier, waiter |
| `/bill/<id>/` | `bill_view` | cashier, waiter |
| `/kitchen/` | `kitchen_view` | chef, manager |
| `/kitchen-data/` | `kitchen_data` (JSON) | chef, manager |
| `/orders/send-kot/` | `send_kot` | cashier, waiter |
| `/orders/bump-kot/<id>/` | `bump_kot` | chef |
| `/orders/printer-status/` | `printer_status` | cashier, waiter |
| `/token-dashboard/` | `token_dashboard` | cashier, waiter (QSR) |
| `/reports/dashboard/` | `reports_dashboard` | owner, manager |
| `/settings/features/` | `feature_flags_view` | superuser only |
| `/settings/features/toggle/` | `toggle_feature_flag` | superuser only |
| `/sw.js` | `serve_sw` | — |

---

## 25. Development Notes

- **Pylance false positives** — all "Cannot find module `django.*`" errors in the IDE are because the `.venv` is not configured in VS Code's Python interpreter setting. The code is correct; the warnings are noise.
- **Business date** — `core/utils.get_business_date(dt, outlet)` returns the correct accounting date. Orders before `outlet.business_day_start_hour` (default 6 AM) count as the previous calendar day. Used for KOT counters, order numbers, and daily metrics.
- **`select_for_update()` pattern** — all sequential counters (daily KOT, daily order number, daily token, promo usage) use a locked row rather than `MAX()+1` to be safe under concurrent requests.
- **Background threads** — KOT printing and WhatsApp receipts run in daemon threads spawned after the HTTP response is returned, so the user never waits for the printer.
