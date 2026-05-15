# Rasova POS — Complete Architecture
**ELI5 Edition. Every piece. Every function. Every URL. ASCII diagrams.**

---

## 1. The Biggest Picture — What Is Rasova?

Rasova is a web application. A restaurant owner opens a browser, logs in, and
uses it like any website. But it runs on your server, not on Facebook's servers.

Here is everything that exists and how it connects:

```
                    ┌─────────────────────────────────────────────────┐
                    │                  THE INTERNET                    │
                    └───────────────────────┬─────────────────────────┘
                                           │
                          Browser sends HTTP request
                          e.g. GET https://rasova.net/billing/
                                           │
                    ┌──────────────────────▼──────────────────────────┐
                    │                    NGINX                         │
                    │           (the traffic director)                 │
                    │                                                  │
                    │  • Handles HTTPS (SSL certificates)              │
                    │  • Serves static files (CSS, JS, images) fast   │
                    │  • Passes everything else to Gunicorn            │
                    └──────────────────────┬──────────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────────┐
                    │                   GUNICORN                       │
                    │            (the Python web server)               │
                    │                                                  │
                    │  • Runs 4-8 Django "workers" in parallel         │
                    │  • Each worker handles one HTTP request at once  │
                    │  • Worker 1: cashier paying a bill               │
                    │  • Worker 2: waiter adding items                 │
                    │  • Worker 3: manager checking reports            │
                    └──────────────────────┬──────────────────────────┘
                                           │
                    ┌──────────────────────▼──────────────────────────┐
                    │                    DJANGO                        │
                    │           (the application itself)               │
                    │                                                  │
                    │  • URL Router → finds the right view function    │
                    │  • View function → runs the business logic       │
                    │  • Templates → turns data into HTML              │
                    └──────┬─────────────────────────┬────────────────┘
                           │                         │
           ┌───────────────▼──────────┐   ┌──────────▼──────────────────┐
           │        POSTGRESQL         │   │           REDIS              │
           │      (the database)       │   │    (the fast memory store)   │
           │                           │   │                              │
           │  • All restaurants' data  │   │  • Celery task queue         │
           │  • Orders, payments,      │   │  • Django session data       │
           │    menus, staff accounts  │   │  • Printer error banners     │
           │  • Permanent storage      │   │  • Cache (fast reads)        │
           └───────────────────────────┘   └──────────────────────────────┘
                                                        │
                                          ┌─────────────▼──────────────────┐
                                          │       CELERY WORKER             │
                                          │  (the background task runner)   │
                                          │                                  │
                                          │  • Reads tasks from Redis        │
                                          │  • Connects to thermal printers  │
                                          │  • Sends ESC/POS bytes           │
                                          │  • Retries if printer fails      │
                                          └──────────────┬───────────────────┘
                                                         │
                                          ┌──────────────▼───────────────────┐
                                          │       THERMAL PRINTER            │
                                          │   (192.168.1.100 port 9100)      │
                                          │                                  │
                                          │  • Epson / Star / Citizen        │
                                          │  • Bill first (full cut)         │
                                          │  • KOTs after (partial cut)      │
                                          └──────────────────────────────────┘
```

---

## 2. A Single HTTP Request — Step by Step

When a cashier types `/billing/` in the browser, here is every single thing
that happens in order, taking roughly 50-200ms total:

```
BROWSER
  │
  │  GET /billing/?table=5
  │  Cookie: sessionid=abc123
  │
  ▼
NGINX
  │  (checks: is this a static file? No. Pass to Gunicorn.)
  │
  ▼
GUNICORN  (picks an idle Django worker)
  │
  ▼
DJANGO MIDDLEWARE STACK  (runs top to bottom, every single request)
  │
  ├─ SecurityMiddleware        checks HTTPS, sets security headers
  ├─ WhitenoiseMiddleware      serves static files if missed by Nginx
  ├─ SessionMiddleware         loads session from Redis/DB → request.session
  ├─ TenantMiddleware          reads subdomain → request.tenant
  ├─ CommonMiddleware          trailing slash handling
  ├─ CsrfMiddleware            validates CSRF token on POST requests
  ├─ AuthenticationMiddleware  reads session → request.user
  ├─ AxesMiddleware            counts failed logins, blocks brute force
  ├─ ContextLoggingMiddleware  adds request_id to logs
  └─ RequestLoggingMiddleware  logs every request (method, path, status)
  │
  ▼
URL ROUTER  (core/urls.py → orders/urls.py)
  │
  │  path("billing/", billing_view, name="billing-view")
  │
  ▼
DECORATOR STACK  (runs before the view function)
  │
  ├─ @login_required      is request.user authenticated? No → redirect /login/
  ├─ @tenant_required     does user have tenant + outlet? No → PermissionDenied
  └─ @feature_required    does this tenant have the feature? No → 403
  │
  ▼
VIEW FUNCTION:  orders/views/billing_views.py → billing_view()
  │
  ├─ reads ?table=5 from URL
  ├─ Table.objects.get(id=5, tenant=..., outlet=...)  ← DB query
  ├─ order_service.get_or_create_open_order(user, table)  ← DB query
  │     └─ checks if open Order exists for this table
  │     └─ if not: creates one, sets table.state = "ordering"
  ├─ MenuItem.objects.filter(tenant=..., outlet=..., is_available=True)  ← DB query
  ├─ MenuCategory.objects.filter(...)  ← DB query
  └─ PaymentConfig.objects.get(outlet=...)  ← DB query
  │
  ▼
TEMPLATE ENGINE
  │
  │  orders/templates/orders/billing.html
  │  extends templates/core/base.html
  │
  ▼
HTTP RESPONSE  (HTML page, ~30-80KB)
  │
  ▼
BROWSER renders the billing screen
```

---

## 3. Multi-Tenancy — How One App Serves 100 Restaurants

This is the most important architectural concept in Rasova.
Every restaurant is a **Tenant**. Every restaurant branch is an **Outlet**.

```
SINGLE RASOVA APPLICATION
           │
           ├── Tenant: Spice Garden (id=1)
           │     ├── Outlet: Main Branch (id=1)
           │     │     ├── Users: Ravi (cashier), Priya (waiter)
           │     │     ├── Tables: T1-T12
           │     │     ├── Menu: 45 items
           │     │     └── Orders: today's orders
           │     │
           │     └── Outlet: Airport Branch (id=2)
           │           ├── Users: different staff
           │           ├── Tables: A1-A8
           │           └── Menu: same + extras
           │
           ├── Tenant: Pizza Palace (id=2)
           │     └── Outlet: Koramangala (id=3)
           │           ├── Users: Ali (owner)
           │           └── Menu: pizza items
           │
           └── Tenant: Chai Corner (id=3)
                 └── Outlet: MG Road (id=4)
```

**How isolation works — every model has tenant + outlet:**

```python
# EVERY database query automatically scoped to this tenant + outlet
Order.objects.filter(
    tenant=request.user.tenant,   # ← Spice Garden only
    outlet=request.user.outlet,   # ← Main Branch only
    status="open"
)
# Spice Garden can NEVER see Pizza Palace's orders
# This is enforced by architecture, not trust
```

**The TenantMiddleware (core/middleware.py):**
```
Request arrives at rasova.net
  │
  ├─ reads request.get_host() → "rasova.net" or "spicegarden.rasova.net"
  ├─ if subdomain: looks up Tenant by slug
  └─ sets request.tenant (used by decorators later)
```

---

## 4. The Database — Every Table and Every Relationship

```
┌─────────────────┐         ┌─────────────────────────────────────────┐
│     TENANT       │         │                  OUTLET                  │
│─────────────────│         │─────────────────────────────────────────│
│ id              │◄────────│ id                                       │
│ name            │  1:many │ tenant_id  (FK → Tenant)                 │
│ slug            │         │ name                                     │
│ tenant_type     │         │ address                                  │
│  fine_dining    │         │ gst_no       (GSTIN, 15 chars)           │
│  franchise      │         │ fssai_no     (14 chars)                  │
│  cafe           │         │ phone                                    │
│ logo            │         │ email                                    │
│ subscription    │         │ whatsapp_no                              │
│  trial/active   │         │ opening_time                             │
│ primary_color   │         │ closing_time                             │
│ font_family     │         │ business_day_start_hour  (default: 6am)  │
│ theme           │         └──────────────────┬──────────────────────┘
└─────────────────┘                            │ 1:many to everything below
                                               │
              ┌────────────────────────────────┼────────────────────────────────┐
              │                                │                                │
              ▼                                ▼                                ▼
┌─────────────────────────┐  ┌─────────────────────────┐  ┌────────────────────────┐
│          USER            │  │         TABLE            │  │      KITCHENSTATION    │
│─────────────────────────│  │─────────────────────────│  │────────────────────────│
│ id                       │  │ id                       │  │ id                     │
│ tenant_id                │  │ tenant_id                │  │ tenant_id              │
│ outlet_id                │  │ outlet_id                │  │ outlet_id              │
│ username                 │  │ name  ("T1", "Window 2") │  │ name ("Grill","Fryer") │
│ first_name               │  │ section  ("Main Hall")   │  │ printer_ip             │
│ last_name                │  │ qr_token  (UUID)         │  │ printer_port  (9100)   │
│ role:                    │  │ state:                   │  │ paper_width_mm (58/80) │
│  owner                   │  │  free                    │  │ cut_type               │
│  manager                 │  │  ordering                │  │  full/partial/none     │
│  cashier                 │  │  preparing               │  │ printer_encoding       │
│  waiter                  │  │  ready                   │  │  cp437/utf-8           │
│  kitchen                 │  │  served                  │  │ is_default (bool)      │
└─────────────────────────┘  │  billing                 │  └────────────────────────┘
                              │  cleaning                │
                              │  merged                  │
                              └───────────┬──────────────┘
                                          │ 1:many
                                          ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                     ORDER                                        │
│─────────────────────────────────────────────────────────────────────────────────│
│ id                                                                               │
│ order_number   ("INV-1-20260513-0005")  ← auto-generated on creation            │
│ tenant_id, outlet_id, table_id, created_by_id                                   │
│ status:   open → billing → closed  (or cancelled)                               │
│ source:   dine_in / takeaway / zomato / swiggy / qr_menu                        │
│ subtotal, gst_total, discount_total, round_off, grand_total  ← all Decimal      │
│ created_at, closed_at, updated_at                                                │
│ aggregator_order_id  (for Zomato/Swiggy webhook orders)                         │
│ customer_name, customer_phone  (optional)                                        │
└───────────────────────────────────────────┬─────────────────────────────────────┘
                                            │
             ┌──────────────────────────────┼──────────────────────────────────┐
             │                              │                                   │
             ▼                              ▼                                   ▼
┌───────────────────────┐  ┌───────────────────────────────┐  ┌───────────────────┐
│       ORDERITEM        │  │           KOTBATCH            │  │      PAYMENT      │
│───────────────────────│  │───────────────────────────────│  │───────────────────│
│ id                     │  │ id                             │  │ id                │
│ order_id               │  │ order_id                       │  │ order_id          │
│ menu_item_id           │  │ tenant_id, outlet_id           │  │ method:           │
│ quantity               │  │ station_id (KitchenStation)    │  │  cash/upi/card    │
│ price  (at time of     │  │ kot_number  (1, 2, 3...)       │  │ amount (Decimal)  │
│   order, not current)  │  │ status:                        │  │ reference         │
│ gst_percentage         │  │  confirmed / bumped            │  │ paid_at           │
│ total_price            │  │                                │  │ created_by_id     │
│ status:                │  └───────────────────────────────┘  └───────────────────┘
│  pending               │               │ 1:many
│  sent (KOT created)    │               │
│  preparing             │               ▼
│  ready                 │  ┌───────────────────────────────┐
│  served                │  │   ORDERITEM  (same table)     │
│  voided                │  │   kot_id links item to KOT    │
│  review (QR approval)  │  └───────────────────────────────┘
│ kot_id                 │
│ notes                  │
│ is_complimentary       │
│ void_reason            │
│ item_discount_pct      │
└───────────────────────┘

┌───────────────────────────────────────────────────────────────────────────────┐
│                               MENUITEM                                         │
│───────────────────────────────────────────────────────────────────────────────│
│ id, tenant_id, outlet_id                                                       │
│ category_id  (FK → MenuCategory)                                               │
│ station_id   (FK → KitchenStation, nullable — null = default station)          │
│ name, price (Decimal), gst_percentage                                          │
│ is_available  (toggle on/off quickly during service)                           │
│ is_veg        (green dot = True, red dot = False)                              │
│ estimated_prep_time  (minutes, shown in KOT and kitchen timer)                 │
│ image  (uploaded photo)                                                         │
│ available_takeaway, available_zomato, available_swiggy  (platform toggles)     │
└───────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. The Order Lifecycle — Fine Dining

This is the most important flow in the entire system.
Every function call and state change is shown.

```
CASHIER/WAITER OPENS BILLING SCREEN
  billing_view()  →  GET /billing/?table=5
        │
        │  order_service.get_or_create_open_order(user, table)
        │    ├─ Order.objects.select_for_update().filter(table=table, status="open")
        │    ├─ if found: return existing order
        │    └─ if not: Order.objects.create(...)
        │              table.state = "ordering"
        │              table.save()
        │
        ▼
  ORDER STATUS: "open"    TABLE STATE: "ordering"
  ─────────────────────────────────────────────────

CASHIER ADDS ITEMS
  (no API call yet — items live in the billing template's JS state)
  (clicking an item adds it to the local cart, no DB write yet)

CASHIER CLICKS "SEND TO KITCHEN"
  send_to_kitchen()  →  POST /send-to-kitchen/42/
        │
        │  kot_service.create_kot(user, order)
        │    ├─ OrderItem.objects.select_for_update().filter(order=order, status="pending")
        │    ├─ groups items by station (Grill items → Grill, Fryer items → Fryer)
        │    ├─ DailyKOTCounter.objects.select_for_update().get_or_create(date=today)
        │    │    └─ counter.value += 1  (atomic, no duplicate KOT numbers)
        │    ├─ KOTBatch.objects.create(order=order, station=station, kot_number=N)
        │    ├─ for each item: item.status = "sent", item.kot = kot_batch
        │    ├─ inventory_service.deduct_inventory_for_items(items)
        │    ├─ table.state = "preparing"
        │    └─ transaction.on_commit → print_kot_task.delay(station_id, order_id, kot_id)
        │         └─ Celery picks this up → printer prints KOT slip
        │
        ▼
  ORDER ITEMS: "sent"    TABLE STATE: "preparing"
  ─────────────────────────────────────────────────

KITCHEN DISPLAY SHOWS THE KOT
  kitchen_data()  →  GET /kitchen-data/
        │
        │  KOTBatch.objects.filter(outlet=outlet, status="confirmed")
        │    .prefetch_related("items__menu_item")
        │  Returns JSON → kitchen.html JS renders cards
        │  Auto-refreshes every 5 seconds
        │
  KITCHEN CLICKS "START" on an item
  start_preparing()  →  POST /item-start/99/
        │  item.status = "preparing"
        │  update_table_state(order.table, order)  ← recalculates table.state
        │
  KITCHEN CLICKS "READY" on an item
  mark_ready()  →  POST /item-ready/99/
        │  item.status = "ready"
        │  update_table_state(...)
        │
        ▼
  ORDER ITEMS: "ready"    TABLE STATE: "ready"
  ─────────────────────────────────────────────────

WAITER SERVES FOOD, MARKS SERVED
  serve_item()  →  POST /serve-item/99/
        │  item.status = "served"
        │
        ▼
  ORDER ITEMS: "served"    TABLE STATE: "served"
  ─────────────────────────────────────────────────

CASHIER GENERATES BILL
  generate_bill()  →  POST /generate-bill/42/
        │
        │  order.recalculate_totals()
        │    ├─ sums all non-voided items: subtotal
        │    ├─ calculates GST per item's gst_percentage
        │    ├─ applies discount_total
        │    ├─ rounds off (round_off field)
        │    └─ sets grand_total = subtotal + gst_total - discount_total + round_off
        │
        │  order.status = "billing"
        │  table.state = "billing"
        │
        ▼
  ORDER STATUS: "billing"    TABLE STATE: "billing"
  ─────────────────────────────────────────────────

CASHIER SEES BILL SCREEN
  bill_view()  →  GET /bill/42/
        │  renders the print preview with all items, totals, GST breakdown
        │  shows payment buttons: Cash, UPI, Card

CASHIER COLLECTS PAYMENT
  pay_order()  →  POST /pay/42/
        │
        │  (inside select_for_update to prevent double payment)
        │  payment_service.process_payment(order, method="upi", amount=395)
        │    ├─ Payment.objects.create(order=order, method="upi", amount=395)
        │    ├─ order.status = "closed"
        │    ├─ order.closed_at = now()
        │    └─ table.state = "cleaning"
        │
        │  print_bill_task.delay(order_id, station_id)  ← Celery queues print
        │
        │  OrderEvent.objects.create(event_type="paid", ...)  ← audit log
        │
        ▼
  ORDER STATUS: "closed"    TABLE STATE: "cleaning"
  ─────────────────────────────────────────────────

CELERY WORKER PRINTS (1 second later, in background)
  print_bill_task(order_id, station_id)
        │
        │  PrintingService.print_bill_with_kots(order, kots)
        │    ├─ _print_bill_body(p, order)   → customer receipt
        │    │    tenant name, outlet, bill number, items, totals, payment method
        │    ├─ p.cut(mode="FULL")            → paper severs, customer takes bill
        │    ├─ for each KOT:
        │    │    _print_kot_body(p, order, kot)  → kitchen copy
        │    └─ p.cut(mode="PART")            → partial cut, KOT chain for kitchen

MANAGER MARKS TABLE CLEAN
  mark_table_cleaned()  →  POST /clean-table/5/
        │  table.state = "free"
        │  Table back to green on floor map
```

---

## 6. The Order Lifecycle — QSR (Token System)

No tables. Customers get a token number. Food ready = token called at counter.

```
COUNTER STAFF CLICKS "NEW ORDER"
  create_and_go_to_billing()  →  POST /token/go/
        │
        │  token_views.assign_token(outlet, tenant, business_date)
        │    ├─ DailyTokenCounter.select_for_update().get_or_create(date=today)
        │    ├─ counter.value += 1
        │    └─ TokenOrder.objects.create(token_number=N, order=order)
        │
        │  redirect to /billing/  (no table, token number shown instead)
        │
        ▼
  TOKEN #42 assigned. All items added, sent to kitchen.
  Kitchen display shows "T#42 — 2x Burger, 1x Fries"
  When food is ready → BUMP → customer called → token #42 come collect!
```

---

## 7. Feature Flags — How Fine Dining ≠ QSR

Different restaurant types have different features.
`core/features.py` defines what each type gets by default:

```
TENANT TYPE          FEATURES INCLUDED
─────────────────────────────────────────────────────────────────
fine_dining    →  floor_plan, waiter_call, kitchen_display,
                  split_bill, merge_tables, table_transfers,
                  waiter_dashboard, ai_menu_import

franchise      →  token_system, kitchen_display, ai_menu_import,
(QSR)             direct_billing_mode, barcode_transfer

cafe           →  floor_plan, token_system, kitchen_display,
                  waiter_call, ai_menu_import
```

**How @feature_required works:**

```python
@feature_required("floor_plan")    # put this on the floor plan view
def table_dashboard(request):
    ...

# Inside the decorator:
if not has_feature(request.user.tenant, "floor_plan"):
    if is_api_request:
        return JsonResponse({"error": "Feature not available"}, status=403)
    raise PermissionDenied   # shows 403 page
```

**Superusers can override per tenant:**
`TenantFeatureOverride` table: force-enable "token_system" for a fine_dining tenant
that has a takeaway counter alongside sit-down tables.

---

## 8. The URL Map — Every Route in the System

```
/ (root)
  landing()  → if logged in: redirect /dashboard/  else: redirect /login/

/login/          login_view()        GET: show form   POST: authenticate + set session
/logout/         logout_view()       POST: clear session, redirect /login/

/dashboard/      owner_dashboard()   Owner/Manager/Cashier only
/dashboard/metrics.json  dashboard_metrics_json()  AJAX, live refresh

/billing/        billing_view()      Main order entry screen
  ?table=N       → opens that table's order
  (no param)     → walk-in / token order

/create-order/           create_order()         POST, creates Order record
/send-to-kitchen/<id>/   send_to_kitchen()      POST, creates KOTBatch, queues print
/send-kitchen-message/<id>/ send_kitchen_message() POST, creates KitchenMessage

/kitchen/        kitchen_view()      Kitchen Display System page
/kitchen-data/   kitchen_data()      GET, JSON, polled every 5s by kitchen.html

/item-start/<id>/  start_preparing()  POST, item.status → "preparing"
/item-ready/<id>/  mark_ready()       POST, item.status → "ready"
/bump-kot/<id>/    bump_kot()         POST, KOTBatch.status → "bumped" (removes from screen)
/serve-item/<id>/  serve_item()       POST, item.status → "served"

/bill/<id>/              bill_view()          GET, shows bill summary for payment
/pay/<id>/               pay_order()          POST, records payment, closes order
/generate-bill/<id>/     generate_bill()      POST, recalculates totals, status → "billing"
/print-bill/<id>/        print_bill_action()  POST, queues print_bill_task in Celery
/print-kot/<id>/         print_kot_action()   POST, re-prints one KOT (for lost slips)
/download-pdf/<id>/      download_pdf_bill()  GET, generates PDF bill for email/WhatsApp
/printer-status/         printer_status()     GET, JSON, checks Redis for printer error

/tables/         table_dashboard()   Floor plan page
/tables-data/    tables_data()       GET, JSON, polled every 5s by tables.html
/manage-table/   manage_table_view() POST, create/edit/delete a table
/clean-table/<id>/       mark_table_cleaned()  POST, table.state → "free"
/available-tables/       available_tables()    GET, JSON, for merge/transfer dropdowns

/cancel-order/<id>/      cancel_order()        POST, order.status → "cancelled"
/cancel-item/<id>/       cancel_item()         POST, item.status → "voided"
/apply-discount/<id>/    apply_discount()      POST, sets order.discount_total
/complimentary-item/<id>/  make_item_complimentary()  POST, item.is_complimentary = True
/item-discount/<id>/     apply_item_discount() POST, per-item discount percentage

/merge-tables/           merge_tables_view()   POST, creates TableMerge record
/unmerge-tables/<id>/    unmerge_tables_view() POST, deletes TableMerge
/transfer-table/         transfer_table_view() POST, moves order to different table

/running-order-items/    running_order_items() GET, JSON, current open order for this user
/order/<id>/             running_order_view()  GET, waiter's order summary page
/order-data/<id>/        running_order_data()  GET, JSON, live data for that order

/refund/<payment_id>/     refund_payment()     POST, creates Refund record (pending approval)
/refund/approve/<id>/     approve_refund_view() POST, manager approves refund
/refund/reject/<id>/      reject_refund_view()  POST, manager rejects refund
/split-pay/<id>/          split_pay()           POST, splits bill across multiple methods
/log-bypass/<id>/         log_bypass()          POST, logs manual override reason

/approve-items/<id>/     approve_items()        POST, approves QR-ordered items (review → pending)

/waiter-dashboard/       waiter_dashboard()     Waiter's notification hub
/resolve-waiter/<id>/    resolve_waiter_call()  POST, dismisses a waiter call
/resolve-kitchen-message/<id>/  resolve_kitchen_message()  POST

/token/                  token_dashboard()      QSR token counter page
/token/new/              create_token_order()   POST, creates order + token
/token/go/               create_and_go_to_billing() POST, creates + goes straight to billing
/token/<id>/bill/        token_billing()        Bill screen for a token order

── API ROUTES (JSON only, no HTML) ─────────────────────────────────
/api/notifications/      notification_api()     GET, polled every 8s by every page
                                                 Returns: waiter_calls, kitchen_messages,
                                                          system notifications
/api/tables/             api_tables()           GET, all tables + state (for mobile clients)
/api/active/             api_active_orders()    GET, all open orders with items
/api/aggregator/webhook/ api_ingest_order()     POST, Zomato/Swiggy webhook entry point

── MENU MODULE (/menu/) ────────────────────────────────────────────
/menu/                   menu_management()      Menu management page
/menu/ai-import/         ai_menu_importer()     POST, Gemini AI reads photo → creates items
/menu/digital-menu/      digital_menu()         Customer-facing QR menu (no login needed)
  ?table_token=XXX       identifies which table is scanning
/menu/gst-management/    gst_management()       GST rates per item
/menu/modifiers/         modifier_management()  Add-ons (extra cheese, no onion, etc.)
/menu/update-item/<id>/  update_menu_item()     POST, edit item details
/menu/create-item/       create_menu_item()     POST, new item
/menu/toggle-item/<id>/  toggle_availability()  POST, is_available flip
/menu/delete-item/<id>/  delete_menu_item()     POST
/menu/toggle-platform/<id>/ toggle_platform()  POST, zomato/swiggy/takeaway visibility

── REPORTS MODULE (/reports/) ──────────────────────────────────────
/reports/dashboard/      dashboard()            Sales reports page
/reports/kitchen/        kitchen_dashboard()    Kitchen performance (avg time per item)
/reports/export/         export_reports()       Downloads Excel/CSV
/reports/api/dashboard/  api_dashboard()        JSON version of reports (for future mobile)

── SETUP MODULE (/setup/) ──────────────────────────────────────────
/setup/                  setup_wizard()         Main settings hub page
/setup/onboard/          onboarding_wizard()    5-step first-time wizard
  ?step=1  Restaurant info (name, type, GSTIN, logo, address)
  ?step=2  Menu (AI import OR sample menu OR manual 3 items)
  ?step=3  Staff (create first cashier/manager)
  ?step=4  Tables (bulk create T1-T12) or skip for QSR
  ?step=5  Payment methods (Cash/UPI/Card, UPI ID)
/setup/sample-menu/      sample_menu()          POST, loads 8 demo items instantly
/setup/checklist/        checklist_status()     GET, JSON, setup completion state
/setup/tables/           setup_tables()         Manage all tables
/setup/menu/             setup_menu()           Menu quick-edit in setup context
/setup/kitchen-stations/ setup_kitchen_stations() Printer configuration
/setup/kitchen-stations/<id>/printer/ update_printer_config()  Set IP, port, cut type
/setup/kitchen-stations/<id>/test-print/ test_print_station() Prints test page
/setup/payment-methods/  setup_payment_methods() Cash/UPI/Card/UPI-ID config
/setup/staff/            setup_staff()          Create/edit/deactivate staff
/setup/outlet/           outlet_settings()      GSTIN, address, hours
/setup/aggregators/      aggregator_setup()     Zomato/Swiggy webhook keys
/setup/promos/           setup_promos()         Discount codes and offers

── ACCOUNTS ────────────────────────────────────────────────────────
/login/                  login_view()
/logout/                 logout_view()
/dashboard/              owner_dashboard()
/settings/features/      feature_flags_view()   Toggle features per tenant
/settings/features/toggle/ toggle_feature_flag() POST

── INVENTORY (/inventory/) ─────────────────────────────────────────
/inventory/board/        inventory_board()      Stock levels per item
/inventory/purchase-orders/  purchase_orders()  PO creation and tracking

── OTHER ───────────────────────────────────────────────────────────
/health/                 health_check()         Returns {"status": "ok"} (for uptime monitors)
/sw.js                   serve_sw()             Service Worker for PWA offline support
/manifest.json                                  PWA install metadata
```

---

## 9. The Printing Pipeline — Every Step

```
TRIGGER: pay_order() or print_bill_action()
         │
         │  print_bill_task.delay(order_id, station_id)
         │    └─ writes JSON message to Redis queue "printing":
         │         {"task": "orders.tasks.print_bill_task",
         │          "args": [42, 1],
         │          "retries": 0}
         │
         ▼ (1-2 seconds later, completely separate process)

CELERY WORKER reads from Redis queue "printing"
         │
         │  print_bill_task(order_id=42, station_id=1)
         │
         ├─ KitchenStation.objects.get(id=1)
         │    └─ station.printer_ip = "192.168.1.100"
         │    └─ station.printer_port = 9100
         │    └─ station.paper_width_mm = 80  (→ 48 chars/line)
         │    └─ station.cut_type = "full"
         │
         ├─ Order.objects.get(id=42)
         │    with items, payments
         │
         ├─ KOTBatch.objects.filter(order=order)
         │    in order of kot_number
         │
         ├─ PrintingService(printer_type="network", host="192.168.1.100")
         │
         ├─ PrintingService.get_printer()
         │    └─ from escpos.printer import Network
         │    └─ Network("192.168.1.100", port=9100)
         │         ← Opens TCP socket to printer on LAN
         │
         ├─ print_bill_with_kots(order, kots)
         │    │
         │    ├─ _print_bill_body(p, order)
         │    │    p.set(align="center", bold=True, double_width=True)
         │    │    p.text("Spice Garden\n")
         │    │    p.set(align="center", bold=False, double_width=False)
         │    │    p.text("Main Branch\n")
         │    │    p.text("────────────────────────────────\n")
         │    │    p.text("Bill : INV-1-20260513-0005\n")
         │    │    p.text("Table: T4\n")
         │    │    p.text("Date : 13/05/2026 20:15\n")
         │    │    p.text("────────────────────────────────\n")
         │    │    for item in order.items:
         │    │        p.text("Butter Chicken          1   280\n")
         │    │    p.text("────────────────────────────────\n")
         │    │    p.text("Subtotal              Rs.280\n")
         │    │    p.text("GST                    Rs.14\n")
         │    │    p.text("TOTAL                 Rs.294\n")
         │    │    p.text("Paid via: CASH\n")
         │    │
         │    ├─ p.cut(mode="FULL")
         │    │    └─ sends ESC/POS bytes \x1d\x56\x00 to printer
         │    │    └─ printer blade motor activates → paper cut → receipt falls out
         │    │
         │    ├─ _print_kot_body(p, order, kot1)
         │    │    p.text("KOT #7\n")
         │    │    p.text("Table: T4\n")
         │    │    p.text("[Grill]\n")
         │    │    p.text("1x  [N] Butter Chicken\n")
         │    │
         │    └─ p.cut(mode="PART")
         │         └─ sends \x1d\x56\x01 → 95% cut → paper stays connected
         │
         ├─ SUCCESS: cache.delete("printer_err_1")  ← clears error banner
         └─ FAILURE: retry in 5 seconds, max 2 retries
              └─ after retries exhausted: cache.set("printer_err_1", {...})
                   └─ next /printer-status/ call returns this error
                   └─ kitchen page shows red "Printer Failure" banner
```

---

## 10. Real-Time Updates — How Screens Stay Current

There is no WebSocket. Everything uses polling (asking every N seconds).

```
KITCHEN DISPLAY  (kitchen.html)
  JavaScript setInterval(loadKitchen, 5000)
    │ every 5 seconds:
    │ fetch('/kitchen-data/')  → GET JSON
    │ kitchen_data() view:
    │   KOTBatch.objects.filter(outlet=..., status="confirmed")
    │   returns all active KOTs as JSON
    │ JS renders/updates the cards
    └─ Result: kitchen sees new orders within 5 seconds of being sent

FLOOR MAP  (tables.html)
  JavaScript setInterval(refreshLayout, 5000)
    │ every 5 seconds:
    │ fetch('/tables-data/')  → GET JSON
    │ tables_data() view:
    │   all tables + their current status (derived from Order, not table.state)
    │   section grouping, elapsed time, cooking items count
    └─ Result: table colours update within 5 seconds of any change

NOTIFICATION BADGE  (base.html — runs on EVERY page)
  JavaScript setInterval(pollNotifications, 8000)
    │ every 8 seconds:
    │ fetch('/api/notifications/')  → GET JSON
    │ notification_api() view:
    │   WaiterCall.objects.filter(outlet=..., is_resolved=False)
    │   KitchenMessage.objects.filter(outlet=..., is_resolved=False)
    │   if waiter role: filter messages by order__created_by=request.user
    │ returns counts + items
    │ JS updates badge count
    └─ if new calls/messages: shows toast + vibrates device + browser notification
```

---

## 11. The Notification Flow — Kitchen to Waiter

```
KITCHEN STAFF sees a problem (ingredient out of stock)
         │
         │  clicks envelope icon on KOT card
         │  msgModal opens with quick-select buttons
         │
         │  submitKitchenMessage()  →  POST /send-kitchen-message/42/
         │    send_kitchen_message() view:
         │      KitchenMessage.objects.create(
         │        order=order,
         │        message="Ingredient out of stock",
         │        tenant=..., outlet=...
         │      )
         │
         ▼ (up to 8 seconds later)

WAITER'S BROWSER polls /api/notifications/ every 8 seconds
         │
         │  notification_api() returns:
         │    kitchen_messages: [{id: 15, table: "T4", message: "Out of stock"}]
         │    count went from 0 → 1
         │
         │  base.html JS detects count increased:
         │    ui.toast("Kitchen (T4): Out of stock", 'info')
         │    navigator.vibrate([100, 50, 100])
         │    sendBrowserNotif("Kitchen Alert — T4", "Out of stock")
         │
         ▼
WAITER sees toast notification. Goes to check with kitchen.
Resolves by clicking Resolved on waiter dashboard.
  resolve_kitchen_message()  →  POST /resolve-kitchen-message/15/
    msg.is_resolved = True
```

---

## 12. The QR Menu Flow — Customer Scans Table QR

```
RESTAURANT sets up tables:
  each Table has a qr_token (UUID, e.g. a3f9b2c1-...)
  QR code encodes: https://rasova.net/menu/digital-menu/?table_token=a3f9b2c1-...

CUSTOMER scans QR with phone
         │
         │  GET /menu/digital-menu/?table_token=a3f9b2c1-...
         │  digital_menu() view:
         │    Table.objects.get(qr_token="a3f9b2c1-...")
         │    MenuItem.objects.filter(tenant=..., is_available=True)
         │    No login required — public page
         │
         │  Renders digital_menu.html:
         │    restaurant logo, name, dark mode toggle
         │    veg/non-veg filter, search, category tabs
         │    item cards with photos, prices
         │    sticky cart bar at bottom (Zomato-style)
         │
CUSTOMER adds items, taps ORDER
         │
         │  POST /create-order/ with table_token + items
         │  create_order() view:
         │    creates Order (status="open")
         │    creates OrderItems (status="review")  ← needs approval!
         │    table.state = "ordering"
         │
         ▼
CASHIER sees "1 need approval" in floor map alert strip
  approve_items()  →  POST /approve-items/42/
    all items with status="review" → status="pending"
    now cashier can send to kitchen

CASHIER sends to kitchen, rest of flow is normal
```

---

## 13. The Multi-Outlet / Aggregator Flow — Zomato/Swiggy Orders

```
ZOMATO SERVER
         │
         │  POST /api/aggregator/webhook/
         │    Headers: X-Signature: hmac_sha256_of_body
         │    Body: {tenant_id: 1, outlet_id: 2, source: "zomato",
         │           aggregator_order_id: "ZOM-8823",
         │           items: [{menu_item_id: 45, quantity: 2}]}
         │
         │  api_ingest_order() view:
         │
         │  1. IP check: is_ip_allowed(request) → checks AGGREGATOR_IP_ALLOWLIST
         │  2. HMAC check: hmac.compare_digest(expected, received_signature)
         │  3. Idempotency: Order.filter(aggregator_order_id="ZOM-8823").exists()?
         │     → yes: return 400 "Order already exists" (prevents duplicates)
         │  4. Create Order (source="zomato", status="paid")
         │  5. Create OrderItems
         │  6. order.recalculate_totals()
         │  7. Payment.objects.create(method="zomato", amount=grand_total)
         │  8. if config.auto_accept_orders:
         │       send_order_to_kitchen(order)  ← auto-KOT
         │  9. if has_feature(tenant, "token_system"):
         │       assign_online_token(order, ...)
         │
         ▼
  Order appears on kitchen display automatically
  Cashier doesn't need to do anything
```

---

## 14. Template Inheritance — How Pages Are Built

Every page in Rasova is built from the same base template:

```
templates/core/base.html  (the master template)
  │
  │  Contains: HTML head, fonts, Bootstrap, CSS variables, header nav,
  │            notification badge, offline banner, global notification poller,
  │            Celery-status printer error banner, setup checklist widget,
  │            dark mode JS, theme system, footer scripts
  │
  ├── orders/templates/orders/billing.html
  │     extends base.html
  │     blocks: title, extra_css, header_left, header_right, content, extra_js
  │
  ├── orders/templates/orders/kitchen.html
  │     extends base.html
  │     forces dark mode via JS on load
  │
  ├── orders/templates/orders/tables.html
  │     extends base.html
  │     adds: floor map CSS, urgency classes, alert strip, cooking badge
  │
  ├── orders/templates/orders/bill.html
  │     extends base.html
  │     shows: bill summary, payment buttons, QR code
  │
  ├── accounts/templates/accounts/owner_dashboard.html
  │     extends base.html
  │     shows: revenue strip, live metrics, quick actions
  │
  ├── menu/templates/menu/menu_management.html
  │     extends base.html
  │     shows: item table, veg dots, AI import modal
  │
  ├── setup/templates/setup/onboard.html
  │     extends base.html
  │     5-step wizard with progress dots
  │
  └── reports/templates/reports/dashboard.html
        extends base.html
        shows: charts, sales tables, exports
```

---

## 15. Roles and Permissions — Who Can Do What

```
PERMISSION MATRIX

Action                        owner  manager  cashier  waiter  kitchen
──────────────────────────────────────────────────────────────────────
View floor plan               ✓      ✓        ✓        ✓       ✗
Take orders (billing screen)  ✓      ✓        ✓        ✓       ✗
Send to kitchen               ✓      ✓        ✓        ✓       ✗
Generate bill                 ✓      ✓        ✓        ✓       ✗
Collect payment               ✓      ✓        ✓        ✗       ✗
Apply discount                ✓      ✓        ✗        ✗       ✗
Make item complimentary       ✓      ✓        ✗        ✗       ✗
Void an item                  ✓      ✓        ✓*       ✗       ✗
Process refund                ✓      ✓        ✗        ✗       ✗
Approve refund                ✓      ✓        ✗        ✗       ✗
View kitchen display          ✓      ✓        ✓        ✓       ✓
Mark item preparing/ready     ✓      ✓        ✓        ✓       ✓
Bump KOT                      ✓      ✓        ✓        ✓       ✓
Message waiter                ✓      ✓        ✓        ✓       ✓
View reports                  ✓      ✓        today    ✗       ✗
Setup (menu, tables, staff)   ✓      ✓        ✗        ✗       ✗
Change feature flags          ✓      ✓        ✗        ✗       ✗
View all restaurants (admin)  superuser only

* cashier can only cancel items they added themselves
```

---

## 16. Celery + Redis — The Background Task System

```
NORMAL REQUEST (fast path)                  BACKGROUND TASK (Celery)
                                            
Django request starts                       (separate OS process, always running)
  │                                          │
  │ does business logic                      │ celery -A core worker
  │ saves to DB                              │   └─ listens to Redis queues
  │                                          │   └─ "default" queue
  │ .delay() writes task                     │   └─ "printing" queue
  │ to Redis in ~1ms                         │
  │                                          │ when a task arrives:
  ▼                                          │   reads args from Redis
Django returns response                      │   loads Django environment
to browser in ~12ms                          │   runs the task function
                                            │   on success: marks done in DB
                                            │   on failure: retries up to 2x
                                            │   after retries: stores error

REDIS holds the message:                   TASK TYPES currently in use:
  {                                          print_kot_task  (queue: printing)
    "task": "orders.tasks.print_kot_task",   print_bill_task (queue: printing)
    "args": [1, 42, 7],
    "kwargs": {},                           RESULTS stored in:
    "retries": 0,                            django_celery_results_taskresult table
    "expires": null                          (django-celery-results app)
  }

FALLBACK (if Redis is down):
  except Exception as celery_exc:
    # Celery unavailable → print synchronously (slow but it works)
    print_kot_task(station_id, order_id, kot_id)
```

---

## 17. The Files — Where Everything Lives

```
f:\pos\
│
├── core/                     Django project settings + config
│   ├── settings.py           ALL settings: DB, Redis, Celery, S3, email
│   ├── urls.py               TOP-LEVEL URL router (includes all app urls)
│   ├── celery.py             Celery app definition (rasova app)
│   ├── __init__.py           imports celery_app so it loads with Django
│   ├── middleware.py         TenantMiddleware, ContextLoggingMiddleware
│   ├── decorators.py         @tenant_required, @feature_required, @role_required
│   ├── features.py           defines which features each tenant_type gets
│   └── views.py              landing page, health check, service worker
│
├── accounts/                 Users, login, dashboard
│   ├── models.py             User model (extends AbstractUser, adds role/tenant/outlet)
│   ├── views.py              login_view, logout_view, owner_dashboard
│   └── urls.py
│
├── orders/                   The heart of the POS
│   ├── models.py             Table, Order, OrderItem, KOTBatch, Payment,
│   │                         WaiterCall, KitchenMessage, OrderEvent, Refund...
│   ├── urls.py               All order-related URLs
│   ├── api.py                JSON APIs: notifications, tables, active orders, webhook
│   ├── tasks.py              Celery tasks: print_kot_task, print_bill_task
│   ├── views/
│   │   ├── billing_views.py  billing_view, pay_order, generate_bill, print_bill_action
│   │   ├── kitchen_views.py  kitchen_view, kitchen_data, start_preparing, mark_ready
│   │   ├── table_views.py    table_dashboard, tables_data, mark_table_cleaned
│   │   ├── order_actions.py  cancel_order, cancel_item
│   │   ├── waiter_views.py   waiter_dashboard, resolve_waiter_call
│   │   ├── token_views.py    token_dashboard, create_token_order, token_billing
│   │   └── promo_views.py    list_active_promos, create_promo
│   └── services/
│       ├── order_service.py      get_or_create_open_order, update_table_state
│       ├── kot_service.py        create_kot (groups items, creates KOTBatch, queues print)
│       ├── payment_service.py    process_payment (atomic, select_for_update)
│       ├── printing_service.py   PrintingService, ConsolePrinter, ESC/POS commands
│       ├── refund_service.py     create_refund, approve_refund
│       ├── void_service.py       void_item
│       ├── inventory_service.py  deduct_inventory_for_items
│       ├── split_service.py      split_bill across multiple payers
│       ├── table_merge_service.py merge_tables, unmerge_tables
│       └── table_transfer_service.py transfer_table (move order to different table)
│
├── menu/                     Menu management + digital QR menu
│   ├── models.py             MenuItem, MenuCategory, MenuItemModifier
│   ├── views.py              menu_management, ai_menu_importer, digital_menu
│   └── urls.py
│
├── setup/                    Configuration for each restaurant
│   ├── models.py             KitchenStation, PaymentConfig, AggregatorConfig
│   ├── views.py              setup_wizard, onboarding_wizard, sample_menu,
│   │                         checklist_status, setup_kitchen_stations
│   └── urls.py
│
├── reports/                  Analytics and exports
│   ├── views.py              dashboard, kitchen_dashboard, export_reports
│   ├── api.py                JSON versions
│   └── services/
│       ├── dashboard_metrics.py  today's revenue, order count, avg order value
│       ├── sales_reports.py      daily/weekly/monthly breakdowns
│       ├── item_reports.py       best sellers, slow movers
│       └── waiter_reports.py     orders per staff member
│
├── tenants/                  Tenant + Outlet models
│   └── models.py             Tenant, Outlet, TenantFeatureOverride
│
├── inventory/                Stock management
│   ├── models.py             InventoryItem, PurchaseOrder, StockTransaction
│   └── views.py
│
├── notifications/            System alerts
│   └── models.py             Notification
│
├── templates/
│   ├── core/base.html        THE MASTER TEMPLATE (everything inherits this)
│   ├── 404.html
│   └── 500.html
│
├── static/
│   └── css/themes/           luxury.css, minimal.css, etc (per-tenant themes)
│
├── ELI5_CELERY_REDIS.md      ← explains Celery + Redis in plain English
├── HOW_TO_USE.md             ← restaurant staff guide
├── STATUS.md                 ← honest project status + roadmap
└── ARCHITECTURE.md           ← this file
```

---

## 18. What Happens on First Load — Service Worker + PWA

```
FIRST VISIT to rasova.net:
  Browser downloads sw.js (service worker)
  Service worker registers itself
  Caches: CSS, JS, fonts, icons

SUBSEQUENT VISITS:
  Service worker intercepts fetch requests
  Static files (CSS/JS): served from cache instantly (no network)
  API calls (/api/*, /billing/, etc): always goes to network (live data)

GOING OFFLINE:
  Static assets still work (from cache)
  Order entry: shows cached billing page
  API calls fail → "offline banner" appears at top
  Currently: orders placed offline are LOST (not queued)
  Planned fix: IndexedDB offline write queue → sync on reconnect

"Add to Home Screen" (mobile):
  manifest.json defines: app name "Rasova POS", icon, theme color, display: standalone
  User can install it on their tablet home screen → looks like a native app
  Opens without browser chrome (no address bar)
```

---

## One-Line Summary of Every Key File

```
core/settings.py          → all configuration (database, redis, celery, media, email)
core/celery.py            → creates the Celery app named "rasova"
core/middleware.py        → TenantMiddleware identifies restaurant from subdomain
core/decorators.py        → @login_required, @tenant_required, @feature_required
core/features.py          → maps tenant_type to list of enabled features
orders/models.py          → Table, Order, OrderItem, KOTBatch, Payment (the core data)
orders/tasks.py           → print_kot_task, print_bill_task (Celery background jobs)
orders/services/kot_service.py  → create_kot (the most critical function: splits items
                                  by station, creates KOT records, queues printing)
orders/services/payment_service.py → process_payment (atomic, prevents double charge)
orders/services/printing_service.py → PrintingService (ESC/POS driver), ConsolePrinter
orders/views/billing_views.py → billing_view, pay_order, generate_bill (the POS core)
orders/views/kitchen_views.py → kitchen_view, kitchen_data (kitchen display)
orders/views/table_views.py   → tables_data (floor map data, status derivation)
orders/api.py                 → notification_api (polled every 8s by every page)
menu/views.py                 → menu_management, ai_menu_importer, digital_menu
setup/views.py                → onboarding_wizard, checklist_status, setup pages
accounts/views.py             → login_view, owner_dashboard
reports/services/dashboard_metrics.py → today's revenue and KPI calculations
templates/core/base.html      → master layout, notification poller, dark mode, themes
```
