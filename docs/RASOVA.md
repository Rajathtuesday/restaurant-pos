# Rasova: the whole system in one file

For any AI assistant or new developer who has to understand Rasova before touching it.
Read this first, then `CLAUDE.md` (working rules), `docs/FRONTEND.md` (screens) and
`CHANGELOG.md` (what changed and why, newest first).

Written 15 November 2026 from the code on branch `qsr`. When this file and the code
disagree, the code wins; fix this file in the same commit.

---

## 1. What Rasova is

Rasova is a point-of-sale and restaurant management system for Indian restaurants:
taking orders, the kitchen, bills and taxes, payments, printing, stock, staff, guests
and reports. It is one Django application that serves many restaurants at once
(multi-tenant SaaS), each on its own subdomain of `rasova.net`.

| Outlet type (`Tenant.tenant_type`) | What it gets by default |
|---|---|
| `fine_dining` | Floor plan, table merge and transfer, waiter calls, kitchen display, split bills, CRM and reservations |
| `pub` | The fine-dining set, plus liquor billed under the state's VAT (never GST) on the same bill as food, the offers engine (happy hour, buy N get M free), liquor hours, bar count, kegs, door covers, a manager's PIN for lowering a printed bill |
| `cafe` | Counter billing with daily tokens, QR menu, composition-scheme bills, parcel charge |
| `franchise` (QSR) | Token counter, Order Ready board, pay-first flow, bill and KOTs on one printed strip |

A type is only a starting set of features: each feature can be switched on or off per
restaurant (section 5).

### Where the business stands

- **No paying restaurant yet.** The code is far ahead of the business.
- **Feature freeze** since 11 November 2026, until the first restaurant pays (in
  `CLAUDE.md`): no new app, feature flag, page or feature model. Allowed: fixes (each
  with a test that fails on the old code), the three deal blockers below, and the review
  plan (isolation walk, scripts out of templates, cutting dead apps, deploy undo,
  settings freeze, docs in the repo). Only the owner can lift the freeze.
- **The three deal blockers** for selling to quick-service counters: a payment gateway,
  offline mode, and setup in under 30 minutes.
- **Focus:** pubs first; fine dining is the core; QSR/cafe as a mode.
- **Entry price:** Rs 999 a month for a single QSR outlet; the first customer gets a
  month free in return for feedback.
- One small cafe has run printing from a phone (the Android app) as a trial.

---

## 2. The whole picture

```
 WHO USES IT                     THE EDGE              ONE AWS EC2 SERVER (t3.micro, Ubuntu 26.04, swap)
 guest phone (QR menu)  --+
 cashier tablet (app)   --+      Cloudflare            nginx :80 --> gunicorn (2 workers x 4 threads)
 kitchen screen         --+-https-> HTTPS for              |
 owner's laptop         --+      *.rasova.net             v
 Android app (WebView)  --+                         Django 6 (one project, Rasova's own apps)
 print app / agent  --(polls for jobs, header X-Agent-Key)--+   |           |            |
                                                        Postgres 18.6   Redis      Celery worker + beat
                                                        (every order,   (sessions, (digests, billing,
                                                         every bill)    limits,     demo reset, PDFs,
                                                                        job queue)  heartbeat)
 OUTSIDE SERVICES
   Cloudflare R2 "rasova-media"    public: logos, dish photos (media.rasova.net)
   Cloudflare R2 "rasova-backups"  PRIVATE: nightly dump, weekly base backup, WAL
   Anthropic (Claude)              AI menu and recipe import
   Razorpay                        UPI QR payments, Rasova's own subscription links, signed webhooks
   Twilio or Meta                  WhatsApp bills (optional)
   Sentry                          errors, when SENTRY_DSN is set
   GitHub Actions                  tests, then deploy over SSH
```

Tech stack: Python 3.12+, Django 6.0, PostgreSQL 18 (CI uses the same major), Redis 7,
Celery 5.6, gunicorn behind nginx, WhiteNoise for static files, django-axes for login
lockout, WeasyPrint for PDFs, python-escpos for thermal printing, the Anthropic SDK for
AI, Playwright for browser tests. The screens are server-rendered templates with plain
ES modules and no build step.

**Counts (15 Nov 2026):** 24 Rasova apps, 75 models, 33 feature flags, 467 URL routes (Django admin included), 206 migrations, about 2,600 Python tests and 330 Node tests.

---

## 3. A request, start to finish

A cashier taps Dispatch: `POST https://spice.rasova.net/create-order/`.

1. Cloudflare ends HTTPS, forwards to nginx, which passes it to gunicorn on `127.0.0.1:8000`.
2. The middleware chain (`core/settings.py`, in order):

| Middleware | Job |
|---|---|
| `CloudflareSchemeMiddleware` | Trusts Cloudflare's "this was https" header, so links and cookies are https |
| `SecurityMiddleware` | HSTS and security headers |
| `BoxMiddleware` | Only on a Rasova Box: first-run setup, licence and clock checks |
| `SearchIndexingMiddleware` | Keeps app pages out of search engines |
| `WhiteNoiseMiddleware` | Serves `/static/` with long cache headers |
| `SessionMiddleware`, `AuthenticationMiddleware` | Who is signed in |
| `TenantMiddleware` | Which restaurant this subdomain is |
| `CsrfViewMiddleware` | A write must carry the CSRF token (cookie `csrftoken2`) |
| `AxesMiddleware` | Locks an account after repeated wrong passwords |
| `SubscriptionStatusMiddleware` | A lapsed Rasova subscription sees the suspended page |
| `ContextLoggingMiddleware` | Sets the current restaurant and outlet (turns on automatic query scoping) and a trace id |
| `SlowQueryMiddleware` (`core/db_limits.py`) | Logs any query over 500 ms (`pos.slow_sql`); web requests have a 15 s statement timeout |
| `NoStoreForAuthenticatedResponsesMiddleware` | Signed-in pages are never stored by a shared cache |
| `RequestLoggingMiddleware` | One line per request: path, status, time, user, restaurant |

3. The view (`orders.views.billing_views.create_order`) checks the rate limit, the cart,
   the table, the source, a manager's PIN if a printed bill is being lowered, then in one
   transaction creates or updates the order, adds the dishes, works out the totals and
   records the request id.
4. The answer is JSON (`{"success": true, "order_id": 41, ...}`); the page's module draws it.

---

## 4. The apps

| Job | Apps |
|---|---|
| Who and where | `accounts` (users, roles, login, the dashboard, the superuser panel), `tenants` (restaurants, outlets, features, branding) |
| Taking orders | `orders` (orders, items, tables, payments, bills, taxes, approvals), `tablemerge`, `tokens` (counter tokens, display board), `menu` (dishes, categories, modifiers, GST classes, the guest QR menu) |
| Kitchen and floor | `kitchen` (KOTs, kitchen display, messages to waiters), `waiter` (guest calls) |
| Money | `payments` (Razorpay QR, refunds), `promos` (whole-bill discounts), `offers` (happy hour, buy N get M), `covers` (cover charge at the door), `finance` (expenses) |
| Stock | `inventory` (ingredients, recipes, suppliers, purchase orders, bar count, kegs, AI recipe import) |
| People | `shifts` (clock in/out, rota, cash register), `crm` (guests, reservations, loyalty) |
| Reading the business | `reports`, `notifications` |
| Setting up | `setup` (setup pages, kitchen stations, printers, staff, payment methods, onboarding) |
| Printing | `printing` (the print queue, the agent's endpoints, printing state) |
| Rasova's own business | `billing` (charging restaurants for Rasova), `agency` (resellers) |
| Elsewhere and tooling | `box` (the offline edition), `devtools` (load tests, demo seeding, signing) |
| Shared | `core` (settings, middleware, decorators, features, Celery, health, AI, PDFs, money formatting) |

Central kitchen and stock requisitions were removed on 13 Nov 2026 (pages, URLs, the
`central_kitchen` and `barcode_transfer` flags); their tables remain until a second
release drops them, and the code is kept under git tag `archive/central-kitchen`.

Outside the Django apps: `agent/` (the PC print agent), `rasova_android/` (the Android
app, Kotlin), `public/` (marketing pages served as static files), `scripts/` (backups,
one-off tools), `systemd/` (service units), `deploy.sh`, `nginx_rasova.conf`.

---

## 5. Restaurants kept apart (multi-tenancy)

```
Tenant  (a restaurant brand: slug "spice", type fine_dining / pub / cafe / franchise)
  +-- Outlet  (a branch: its own tables, menu, stations, printers, bill series, tax setup)
  +-- User    (one tenant, one outlet, one role)
```

Roles (`accounts.User.role`): `owner`, `manager`, `cashier`, `captain`, `waiter`, `chef`,
`kitchen`, and `agent` (a Rasova sales role, never assignable by a restaurant). A
superuser is Rasova staff and sees the `/superuser/` panel.

Three layers keep one restaurant out of another's rows:

1. **Every query names its restaurant.** Views filter by `tenant=request.user.tenant,
   outlet=request.user.outlet`. Another restaurant's id answers 404.
2. **Automatic scoping as a net.** Restaurant models inherit `TenantScopedModel`; its
   manager (`core/models.py`) adds the current restaurant to every query during a
   signed-in request. A system check fails startup if a model opts out. Seeing every
   restaurant on purpose needs `.unscoped(reason=...)`, and a test
   (`core/tests/test_core.py`) holds those call sites to a reviewed list (by line).
3. **The walk.** `core/tests/test_tenant_walk.py` visits every URL as restaurant A with
   restaurant B's ids and fails on any answer that is not a refusal or that contains B's
   marker text. A new URL with an id must be mapped in its `REGISTRY`, or listed in
   `PUBLIC_BY_DESIGN` with a reason. Its `Restaurant` class is a ready, fully set-up
   fixture used by many tests.

Not there yet: Postgres row-level security (isolation lives in the code).

**Features.** `core/features.py`: `TENANT_FEATURES[tenant_type]` is the default set,
`TenantFeatureOverride` switches one feature per restaurant. Views use
`@feature_required("x")`, templates `{% if 'x' in tenant_features %}`.
`@role_required` and `@feature_required` record what they allow on the view, and
`core.decorators.may(user, view)` asks it, so a page's data never copies role lists.
Feature overrides are cached on the tenant object (a test that switches one mid-test
must re-read the user).

The 33 features: `advanced_reports`, `ai_menu_import`, `ai_recipe_import`,
`composition_scheme`, `counter_billing`, `cover_charge`, `crm`, `direct_billing_mode`,
`floor_plan`, `gstr_export`, `guest_feedback`, `inventory`, `kitchen_display`,
`kot_system`, `liquor_vat`, `loyalty_points`, `merge_tables`, `modifiers`,
`multi_kitchen`, `offers`, `parcel_charge`, `platform_sync`, `purchase_orders`,
`qr_menu`, `razorpay_gateway`, `reports`, `reservations`, `running_order`,
`simple_billing`, `split_bill`, `token_system`, `waiter_call`, `whatsapp_receipts`.
Defaults per type: fine dining 21, pub 23, cafe 15, franchise 14.

**The business day.** A pub that closes at 2 am is still on Friday.
`core.utils.get_business_date` uses each outlet's day-start hour; reports, token numbers,
offer windows and promo dates count by business date. Bill numbers run per Indian
financial year (April to March), the year also taken from the business date.

---

## 6. Money

```
order (open) --> items (dish, qty, modifiers, notes, the tax rule at that moment)
   |
   +--> totals: orders/services/tax_engine.py, the ONLY place tax is worked out
   |      GST 0/5/18%, inclusive or on top, composition scheme, no GSTIN = no GST,
   |      liquor VAT (Karnataka 0% at the bar), parcel charge and its GST,
   |      offers, promos, discounts, round-off to whole rupees
   |
   +--> bill issued: number from the outlet's BillSeries (CODE/2627/000123, <= 16 chars,
   |      GST Rule 46), never reused; an issued bill is never re-totalled (IssuedBillError);
   |      lowering a printed bill needs a manager's PIN (orders/services/approvals.py)
   |
   +--> payment: cash | UPI (Razorpay QR, signed webhook) | card | split | part payments
   |      refunds by request and approval (payments/refund_service.py)
   v
order (paid, closed) --> reports, Z-report, daily digest email
```

- **One rule in two places, held equal.** The cart shows totals before the server makes
  the bill, so the tax and offer engines also exist in the browser
  (`static/js/cart_tax.js`, `static/js/offers.js`). Parity tests run both on the same
  carts and fail on any difference. The parcel rule likewise (`orders/services/parcel.py`
  and `static/js/billing/parcel.js`).
- **Golden bills.** `orders/tests/golden` holds bills worked out by hand for each tax
  situation (including 1,000 pub bills); the engine must match them to the paisa.
- **One bill layout.** `orders/services/bill_layout.py` builds the money lines for the
  printed, thermal, A4, PDF, WhatsApp and split bills alike.
- **One discount writer.** `orders/services/bill_discount.py`. A typed discount needs a
  reason; cashiers and captains are held to the outlet's staff discount limit.
- **Offers** (`offers/engine.py`): buy N get M free, % off, flat off, on dishes or
  categories, in clock-hour windows on chosen days; a line keeps the offer that was live
  when it was added. Set up at `/setup/offers/` with a live example bill.
- **Promos** (`promos/`): whole-bill % or flat off, optional code, minimum (the promo
  pauses while the bill is below it), dates, usage cap.
- **Exactly once.** Every order request carries an id (`request_id`, a UUID made on the
  screen, `static/js/core/request_id.js`); the server keeps it (`orders.OrderRequest`,
  unique per outlet) and answers a repeat with the first order. Every payment carries one
  too (`Payment.request_key`, unique per order): an offline or dropped cash payment sent
  again is answered, never recorded twice. A gateway payment's reference is unique in the
  database.
- **Money on screen** is written one way (`static/js/core/money.js`, `core/money.py`).

---

## 7. Kitchen

Dispatch sends the new dishes as a KOT batch per kitchen station (Hot Kitchen, Bar...).
The kitchen display (`/kitchen/`) asks the server every 5 seconds for tickets with
unfinished dishes (two queries however long the history). Cooks tap Start Prep, Ready,
or Bump for a whole ticket. A guest's QR order waits for staff approval before it reaches
the kitchen. At a counter (cafe, QSR) the KOT fires at payment. A station marked "made as
soon as sent" (a bar) treats a cancelled sent drink as poured: wastage, with a manager's
PIN.

---

## 8. Printing

```
the server queues a PrintJob (ESC/POS bytes, the printer's IP)
     |  every 2 s: GET /orders/agent/jobs/  (header X-Agent-Key = the outlet's secret)
     v
a print app on the restaurant's network
  - the Rasova Android app (a foreground service): the normal way
  - the PC agent (agent/rasova_agent.py), or Termux on a phone
     |  TCP 9100 on the restaurant's Wi-Fi
     v
the thermal printer --> the app reports done, or failed with the reason
```

The server cannot reach a printer inside a restaurant, so the print app asks for work.
An idle ask costs one Redis read. The server records when an agent last asked, and the
billing, floor plan and token screens show a printer chip (ready, not seen for N minutes,
jobs stuck, last print failed). Setup's Test Print waits for the printer's real result.
Strip mode (QSR): bill, partial cut, each KOT, full cut. On an iPhone the bill page
opens for AirPrint. On the Box, the Box prints straight to the printers.
`python manage.py preview_print <order_id>` shows the exact output without a printer.
Details: `PRINTING_SYSTEM.md`, `docs/PRINTER_SETUP.md`.

---

## 9. Offline

Two kinds:

**Cloud Rasova** keeps a tablet working through an outage: "keep taking orders and cash,
lose nothing, duplicate nothing".
- The service worker (`static/sw.js`) keeps pages and their files: hashed static files
  cache-first, unhashed ones network-first, app pages network-first with the kept copy as
  the fallback; it pre-warms `/dashboard/`, `/billing/`, `/kitchen/` on install. `/api/`,
  admin and writes are never touched. No Background Sync (unreliable in the Android
  WebView): the page replays the queues itself.
- Two IndexedDB queues (`static/js/core/offline.js`): new orders, and cash payments.
  A refused order is dropped with a message; a payment is money, only ever replayed
  against its own order, and a refusal is shown for the cashier to settle.
- What does not work offline: printing, the kitchen screen updating, UPI and card (only
  the gateway can confirm money), reports, setup, logging in.

**The Rasova Box** (`box/`, `RASOVA_EDITION=box`) is the whole app on a mini PC in the
restaurant at `http://rasova.local`, no internet needed: code compiled with Cython (no
source on the box), an Ed25519 licence checked offline, nightly backup to a drive with
restore and undo, signed updates from a pen drive with automatic rollback, printing
straight to the printers. Internet features are off on a box. Built, but parked (no
hardware, no customer asking). Guide: `docs/BOX_GUIDE.md`.

---

## 10. The screens (frontend)

Rules in `docs/FRONTEND.md`. In short:

- **Server-rendered pages** on one shell, `templates/core/base.html` (the one header, the
  theme, toasts, the offline banner); guest pages on `templates/core/public_base.html`.
  A page fills the header slots `page_title`, `page_outlet`, `header_links`,
  `header_badges`, `header_theme`, `header_status` (Django silently ignores a block the
  parent does not have; `core/tests/test_one_header.py` catches that).
- **Plain ES modules, no build step**, under `static/js/<page>/`. A page's server data
  arrives once as JSON: `{{ page_data|json_script:"page-data" }}` from a
  `<page>_page_data()` function next to the view, with addresses built by `reverse()`
  (0 stands in for an id; `withId()` in `static/js/billing/urls.js` fills it).
- Buttons carry `data-action` and one delegated listener handles them
  (`delegate()` in `static/js/core/dom.js`). Common jobs need no page script:
  `data-action="post"` buttons and forms (`static/js/core/post_actions.js`: confirm, ask,
  remove a row, reload, go) and shared change/input actions (`static/js/core/page_actions.js`).
- Shared objects (`api`, `ui`, `theme`, `platform`, `orderQueue`, `paymentQueue`) are
  imported from `static/js/core/app.js`. Nothing is published on `window`
  (`core/tests/test_static_modules.py` fails on any `window.x =`).
- **Never `alert()`, `confirm()` or `prompt()`**: they never show inside the Android app.
  Use `ui.toast`, `ui.confirm` (title shown as text) and `ui.ask`.
- **Escape what people typed**: build elements with `textContent`, or `esc()` from
  `static/js/order_edit.js`.
- **No inline script, style block or onclick in any template.** The ratchet
  (`core/tests/test_frontend_hygiene.py`) is empty since 15 Nov 2026 and fails on any new
  one; it also fails on an em dash in a template.
- Styles in `static/css/<app>/<template>.css`; colours as tokens in `static/css/luxury.css`
  (light and dark; Bootstrap's `--bs-*` mapped to them).
- Third-party files live in `static/vendor/` with versions and hashes (`static/vendor/VENDOR.md`); no
  outside host is loaded by any page (a test checks). QR codes are drawn locally.
- Polling pages compare before redrawing, draw only the newest answer, ignore a second
  tap while the first is out, and never show a failure as "empty".
- The Android app (`rasova_android/`) is a WebView around the same pages with a JS
  bridge (`JSBridge.kt`) and the print service. It reports its version;
  `core/app_version.py` shows an update banner below `MINIMUM_ANDROID_APP`.
- Gotcha: Django rewrites JS module imports to hashed names at collectstatic
  (`core/storage.py`). A comment containing "export " or "import " above an import makes
  it rewrite that import twice; reword the comment.

---

## 11. AI (Claude)

`core/ai_service.py`, Anthropic SDK (`anthropic` in `requirements.txt`), since 15 Nov 2026
(Gemini before; never add another provider back).

| Task | Model (override in `.env`) |
|---|---|
| Read a menu from a photo, PDF, Word, Excel, CSV or pasted text | `claude-sonnet-5-5` (`AI_MENU_MODEL`), with the API's server-side refusal fallback |
| Read a recipe, match ingredients to inventory, suggest a price | `claude-haiku-5-5` (`AI_RECIPE_MODEL`) |

- Answers come back through structured outputs (a JSON schema per task). A refusal or a
  cut-off answer is a failure, and each task has its own fallback: a plain regex parser
  for menus with text, one clear message for recipes, "unmatched" for ingredients.
- The client is built only from `ANTHROPIC_API_KEY`; without it the AI is off (the SDK's
  login-profile fallback is not used). Tests pass a fake client:
  `AIService(client=FakeClaude(...))` (`core/tests/fake_claude.py`). No test calls the
  real API.
- Menu import runs in Celery (`menu/tasks.py`); recipe import is a job with a review page
  (`inventory/tasks.py`, `/inventory/recipe-import/<id>/review/`).

---

## 12. Background jobs (Celery)

| When | Job |
|---|---|
| Every minute | `core.tasks.heartbeat`: proof the worker and beat are alive (read by `/health/`) |
| Every 2 hours | Reset the public live-demo restaurant |
| 06:00 | Rasova's monthly subscription invoices |
| 06:30 | Suspend overdue subscriptions |
| 07:00 | Daily digest email to subscribed owners |
| On demand | PDFs, print jobs, menu and recipe imports, low-stock alerts (sent on commit) |

Beat is its own systemd service (`systemd/celery-beat.service`). If Redis is unreachable
in development, work falls back to running synchronously.

---

## 13. Deploy, health, backups

**Deploy.** `git push origin qsr` runs GitHub Actions (`.github/workflows/ci.yml`): ruff,
the system check, the whole test suite on Postgres and Redis, browser tests in Chromium.
If green, it SSHes to the server and runs that commit's `deploy.sh`: pull, install
requirements, migrate, collectstatic when static changed, restart gunicorn, redis,
celery. `deploy.sh` saves the previous state in `versions/last/` (including a database
dump) and undoes itself if the new version fails its checks; `deploy.sh --undo` by hand.
Migrations must only add (`core/tests/test_migrations_additive.py`; a removing one is
listed in `ALLOWED` with its reason), so the previous release can always run on the new
schema. Runbook: `docs/DEPLOY.md`; what to do when things break on a Saturday night:
`docs/RUNBOOK.md`.

**Health.** `/health/` answers a load balancer with one line. With the header
`X-Health-Token` (server `.env` `HEALTH_TOKEN`) or as a superuser it reports the database,
Redis, the worker and beat heartbeat, disk, swap, connections, the last good backup and
stuck printing; any failure makes it 503 (`core/health.py`).

**Backups** (`scripts/backup/`, `docs/MEDIA_AND_BACKUPS.md`): nightly `pg_dump` to the
private R2 bucket (30 days), a weekly base backup, continuous WAL archiving, a WAL health
check every 15 minutes, and a restore drill script. At most minutes of data are at risk.

**Server.** One AWS t3.micro (1 vCPU, 1 GB, swap), Ubuntu 26.04, Postgres 18.6. An
October load test put it at about 20 restaurants in a burst, 10 through a day, about 2
once CPU credits run out: right for zero customers, to be resized before the first few.

---

## 14. Security, in short

- HTTPS everywhere (Cloudflare "Always Use HTTPS", HSTS), secure cookies.
- Login lockout (django-axes); rate limits on login, order creation and guest actions.
- Guests reach only their own things through signed tokens (a table's QR token, a signed
  bill link, feedback, order status).
- The print agent's secret travels in a header, never a URL.
- Razorpay webhooks are checked by signature.
- A printed bill cannot be lowered without a manager's PIN; every change after printing is
  recorded with whose PIN approved it.
- Server errors never show internal text to the screen.
- Encrypted model fields need `FIELD_ENCRYPTION_KEY` (a Fernet key).
- Under the test runner, every outside-service secret is blanked before settings read
  them (`core/tests/test_no_real_services.py`).

---

## 15. Tests

| Kind | What it proves | Run |
|---|---|---|
| Django suite | Views, models, money, permissions; every fix has a test that failed on the old code | `python manage.py test --parallel 8` (about 9 minutes) |
| Fast set (about 430 tests) | Money, isolation, the frontend guards; run before every commit | `python -m core.fast_tests --keepdb` (about 90 s) |
| Golden bills, parity tests | The tax engine to the paisa; the browser's copies equal to the server's | part of the suite |
| The tenant walk | No URL shows one restaurant another's rows | `core.tests.test_tenant_walk` |
| Node module tests | The screens' logic with fakes: refused, offline, late answer, double tap, escaping | `node --test --test-reporter=tap "static/js/tests/*.test.mjs"` |
| Browser tests (Playwright, Chromium) | Real pages, real taps (`core/tests/browser.py`, `BrowserTestCase.run_page`) | part of the suite |
| The smoke walk | Every owner page loads with no error, no missing file, one header | `core.tests.test_browser_smoke_walk` |
| Colour walk, text floor, empty states | Light and dark readable, 12 px minimum, no blank first-day page | part of the suite |
| Screenshot sheet | Forty pictures for a person to look at before a push | `SCREENSHOTS=1 python manage.py test core.tests.test_screenshot_sheet` |

Notes: one `manage.py test` at a time (they share `test_pos_db`); after adding a
migration, drop `--keepdb` once; tests hash passwords with MD5 for speed. Strategy:
`docs/TESTING_STRATEGY.md`.

---

## 16. Working rules (for any assistant)

From `CLAUDE.md` and the owner's standing rules:

- **The feature freeze** (section 1). If a request would add a feature, say so and point
  at the rule before writing code.
- **Fixes come with a test that fails on the old code**, proved by running it against
  the old code. Fix the root cause, not the symptom.
- **No em dashes anywhere**: code, comments, docs, commit messages.
- **Commit messages carry no Co-Authored-By trailer** and never mention AI. A
  `core/settings.py` change states its reason in the commit message.
- **`CHANGELOG.md` gets an entry for every shipped change**, newest first.
- **Never push without the owner's word.** He opens the screens himself first.
- A new URL that takes an id goes into the tenant walk's `REGISTRY`.
- Personal notes and working pages go in `md_files/` (git-ignored); product docs live in
  the repo (`docs/`, `PRINTING_SYSTEM.md`).
- Visual fixes must hold after another screen and back, in light and dark, after a reload
  (suspect the service worker first if a change seems not to apply).
- Put a helper function above a view's decorators, never between them and the `def`
  (it would take the decorators and leave the view unprotected).
- Django `{# #}` comments cannot span lines; use `{% comment %}`.

---

## 17. Code map

| Looking for | Go to |
|---|---|
| Settings, middleware, the beat schedule | `core/settings.py`, `core/middleware.py` |
| Tenant scoping | `core/models.py`, `core/tenant_context.py`, `tenants/checks.py` |
| Features and permissions | `core/features.py`, `core/decorators.py` |
| Taking an order | `orders/views/billing_views.py`, `orders/services/order_service.py` |
| Taxes and totals | `orders/services/tax_engine.py`, `orders/services/tax_service.py`, `static/js/cart_tax.js` |
| Bills, approvals, payments | `orders/services/bill_numbers.py`, `orders/services/approvals.py`, `orders/views/payment_views.py`, `payments/` |
| Offers, promos | `offers/engine.py`, `static/js/offers.js`, `promos/`, `orders/services/bill_discount.py` |
| Kitchen | `kitchen/services/`, `static/js/kitchen/` |
| Printing | `printing/`, `agent/`, `rasova_android/`, `PRINTING_SYSTEM.md` |
| The page shell and modules | `templates/core/base.html`, `static/js/core/`, `docs/FRONTEND.md` |
| A page's script | `static/js/<page>/` (billing, bill, tables, kitchen, running_order, live_orders, order_history, qr_menu, menu, setup, inventory, reports...) |
| Offline | `static/sw.js`, `static/js/core/offline.js`, `static/js/core/request_id.js` |
| AI | `core/ai_service.py`, `menu/tasks.py`, `inventory/tasks.py` |
| Health | `core/health.py`, `core/tasks.py`, `core/db_limits.py` |
| Deploy and server | `deploy.sh`, `systemd/`, `nginx_rasova.conf`, `docs/DEPLOY.md`, `docs/RUNBOOK.md` |
| Backups | `scripts/backup/`, `docs/MEDIA_AND_BACKUPS.md` |
| The Box | `box/`, `docs/BOX_GUIDE.md` |
| Demo data | `python manage.py seed_pub_demo` (a Friday night at a pub; `--no-night`) |
| What changed and why | `CHANGELOG.md` |

Other docs in `docs/`: `USER_MANUAL.md`, `SUBSCRIPTION_TIERS.md`, `MARKET_COMPARISON.md`,
`LOAD_TESTING.md`, `THEME_SYSTEM.md`, `TEMPLATE_INHERITANCE.md`, `PRINTER_SETUP.md`,
`ELI5_PRINTING_ARCHITECTURE.md`, `parcel_charge_explainer.md`.

---

## 18. Weak points and what is open (15 Nov 2026)

- **One small server** (section 13): resize before the first few customers.
- **Isolation in code, not in the database** (no row-level security).
- **Cloud printing needs the internet**; the Box answers it and is parked.
- **A large surface for no customers**: many apps and features to keep working by one
  person; which to cut is an open plan (P2).
- Not yet done on the server: an outside monitor on `/health/` with `HEALTH_TOKEN`, a
  rehearsed restore, confirming Sentry, a separate development R2 bucket, adding
  `ANTHROPIC_API_KEY`.
- The three deal blockers: payment gateway (not started), offline mode (orders and cash
  queue offline; printing does not), setup under 30 minutes (not measured end to end).
- Owner decisions pending: a served dish could become editable with a manager's PIN; a
  per-item parcel charge does not rise for dishes added later; Kitchen Bump skips dishes
  only "sent".

---

## 19. Words used in the code

| Word | Meaning |
|---|---|
| KOT | Kitchen Order Ticket: the slip (or screen ticket) telling a station what to cook |
| Station | A kitchen section with its own printer (Hot Kitchen, Bar) |
| Business day | The trading day, starting at the outlet's day-start hour |
| Token | A counter order's daily number (cafe, QSR) |
| Composition scheme | A small-business GST scheme: a Bill of Supply, no GST lines |
| Liquor VAT | State VAT on alcohol, never GST; Karnataka 0% at the bar |
| Comp | A complimentary (free) dish |
| Void | A dish taken off an order, with a reason |
| Parcel charge | A charge for takeaway packing, per bill or per item |
| Covers | A cover charge paid at the door, used against the bill |
| The ratchet | The frontend hygiene test that only lets inline code shrink (now empty) |
| The walk | The tenant isolation test over every URL |
| Page data | The JSON a view gives its page's script |
| The agent | The print app (Android or PC) that polls for print jobs |
| The Box | The offline, on-premises edition |
