# Changelog

All notable changes to Rasova are recorded here, newest first. This file starts
2026-09-05 — it is not a retroactive rewrite of the full project history.
For anything earlier than the "Recent history" section below, `git log` is
the source of truth.

---

## 2026-09-24

### Added
- **Reduce the quantity of a dish that is already with the kitchen** - "make that one naan, not two" used to mean cancelling the whole line and adding it again. New `reduce_item_quantity` in `orders/services/void_service.py` and `POST /reduce-item/<id>/` (body `{"reduce_by": 1, "reason": "..."}`, same roles as cancel: owner, manager, cashier, captain). Before the dish is sent it is a plain basket edit (quantity drops, no void record, stock untouched). After it is sent, the removed units are split off into their own voided line with the same price, GST, discount, modifiers and KOT, and the original line keeps the rest, so the void report shows exactly what was taken back and why, every sales report (which already skips voided lines) stays right, and stock comes back for only the removed units, add-ons included. No schema change. Removing the whole quantity is the same as cancelling the line. The same rules as cancelling apply: nothing on a paid, closed or cancelled order, and a served dish needs a manager or owner. The partial reduction is logged as `item_voided` (with `partial`, `quantity`, `from`, `to`) so the Discount & Void audit counts it. Amounts must be whole numbers: `1.5`, `"1.5"`, `true` and junk are refused instead of being rounded.
- **Live Orders board** (`/live-orders/`, data at `/live-orders/data/`) - every open order at the outlet on one screen, as tickets with their dishes, add-ons, notes, status, age (amber at 20 minutes, red at 40) and total, plus Add, Bill and Details links. Filters for dine-in, counter, online and "needs attention" (a dish waiting for approval or ready to serve, or an order open 30+ minutes), and a search box. Removing one of a dish or cancelling it opens a small sheet that asks for a reason (quick choices or typed) when the dish is already with the kitchen. Refreshes every 8 seconds and pauses while that sheet is open or the tab is hidden. Served dishes show a lock for anyone below manager. Open to owner, manager, cashier, captain (can edit) and waiter (view only), for tenants with either `running_order` or `token_system`. Linked from the owner dashboard, the floor plan and the token dashboard. The data endpoint costs 8 queries per poll whether 1 or 10 orders are open.
- **Shared edit sheet** - `static/js/order_edit.js`, used by both the board and the Running Order page so the two behave the same.

### Changed
- **Cancelling a dish now records why** - `cancel-item` accepts an optional `{"reason": "..."}`. Every cancel used to be saved as "Manual Item Cancellation", which made the void report's reason breakdown useless. Calls without a body keep the old default.
- **Running Order page** - gets the same remove-one button and reason sheet, lists cancelled dishes underneath (struck through, with the reason), and uses Rasova's pop-ups instead of the browser's plain `confirm()`/`alert()`. `running-order-data` now includes `in_kitchen` and `void_reason` for each line.

### Fixed
- **Reprinting a kitchen ticket brought back cancelled dishes** - `_print_kot_body` printed every line ever attached to the KOT, including voided ones, so a reprint after a cancel told the kitchen to cook the cancelled dish again. It now skips voided lines. Both print paths (server and phone agent) share this body.
- **The WhatsApp receipt listed cancelled dishes** - `_build_message` listed every line with its price while the total left cancelled ones out, so the guest saw dishes they were not charged for. It now skips voided lines, like every printed and on-screen bill already did.
- **New scripts in `static/js/` were silently never committed** - the `.gitignore` rule `js/` was meant for the personal `js/` folder at the repo root but matched any folder named `js`, including `static/js/`. A new script there worked locally and would have been missing after deploy (the July QR library had to be force-added for the same reason). Anchored to `/js/`: the root folder stays ignored, app scripts are tracked.

### Tests
- **43 new tests.** `orders/tests/test_reduce_quantity.py` (28): the split into an active and a voided line, stock back for only the removed units (add-ons included), basket edit before the kitchen, whole quantity equals cancel, refused amounts, served-dish manager rule, paid orders locked, waiter/chef/kitchen refused, GET refused, login required, another tenant's and another branch's line is a 404 and stays untouched (for cancel too), reducing 3 to 2 bills exactly like ordering 2 in both GST modes with a per-dish discount, the kitchen display shows the new quantity, a reprinted ticket leaves out cancelled units, top-items counts only what was sold, and cancel records the reason given. Four of them fire real simultaneous HTTP requests at one line (two staff removing one of two, two racing for the last one, remove and cancel at the same moment, nine requests for five units): each unit is removed exactly once, stock comes back exactly once, and the total ends right. `orders/tests/test_live_orders_board.py` (15): only open orders of the user's own outlet, never another tenant's or branch's, voided lines hidden, summary counts, links, edit flags per role, the feature gate, token labels for cafes, and a query count that stays at 8 whether 1 or 10 orders are open.
- **Verified** on Postgres with the full suite (1,409 tests, all passing) and with a real-browser run on the local demo restaurant (20 of 20 checks, desktop and phone, light and dark: remove one with a required reason, the board and the Running Order page update straight away, the database holds the voided unit with its reason, search and filters, no sideways scroll and 44px tap targets on a phone, a lock instead of edit buttons on served dishes for a cashier, no JavaScript errors).

---

## 2026-09-21

### Changed
- **Dependency updates, batch 1 (patch and minor releases inside the same major version)** - cryptography 50.0.0 to 50.0.1, django-storages 1.14.2 to 1.14.6, psutil 7.1.0 to 7.2.2, psycopg2-binary 2.9.11 to 2.9.13, pypdf 6.17.0 to 6.19.0, rapidfuzz 3.14.1 to 3.14.6, requests 2.33.0 to 2.34.2, qrcode 8.0 to 8.2. `pip-audit` reports no known vulnerabilities for the pinned versions. Verified with the full suite (1,360 tests) on Postgres 18, the production version, using a separate virtual environment so the working environment was never touched.
- **Removed `django-redis` from `requirements.txt`** - nothing imports it: `core/settings.py` uses Django's built-in `django.core.cache.backends.redis.RedisCache`, which needs only the `redis` package. `qrcode` stays even though the app never imports it, because `python-escpos` depends on it. Removing a line from the file does not uninstall the package from an environment that already has it.
- **Dependency updates, batch 2 (runtime-facing)** - gunicorn 22.0.0 to 26.2.0, sentry-sdk 2.20.0 to 2.69.2, redis 7.4.0 to 7.4.1. The unit tests cannot cover these, because the test settings deliberately run on an in-memory cache and database sessions, so they were checked for real in Docker (Python 3.13, Postgres 16, Redis 7, the CI setup): the full suite (1,360 tests) passes, gunicorn starts with the exact flags from `systemd/gunicorn.service` (2 workers, 4 threads, gthread) and survives a graceful reload (`HUP`) and a clean shutdown, a login session created by one worker is honoured by the other through the Redis-backed cache, a Celery task runs through the Redis broker and stores its result, and Sentry initialises and captures. `pip-audit` reports no known vulnerabilities.
- **Worth knowing about gunicorn 26** - it opens a control socket at `~/.gunicorn/gunicorn.ctl`. If that folder is not writable it logs a `Control server error` and keeps serving normally; `--no-control-socket` turns the feature off if the noise is unwanted. The `ubuntu` user's home folder is writable, so production should not notice.
- **Deliberately not bumped:** boto3 (the backups depend on it and newer versions changed how uploads are checksummed, so it needs its own real-R2 test), django-axes 8, google-genai 2, Django 6.1 and redis-py 8 (each is a major or feature release that needs separate testing).

### Fixed
- **README corrections** - the link to this file pointed at `CHANGokELOG.md` (a typo). The AI import key was documented as `GEMINI_API_KEY` but the code reads `GOOGLE_API_KEY`, so anyone following the README got AI menu import silently disabled. Stale counts: tests were "797+" (now 1,360), orders services "14" (9), orders tests "246" (414), Django 6.0.3 (6.0), PostgreSQL 16 (16 in CI, 18 in production). The project structure listed 14 of the 22 apps. About 30 real environment variables were undocumented (storage, backups, Razorpay billing keys, Twilio, email, encryption key, hardening options). The roadmap still listed subscription billing and WhatsApp bill delivery as not started, although both shipped. A new "Backups and Media Storage" section describes the nightly dump, WAL archiving and point-in-time recovery, the 15-minute health check and the restore drill, and links `MEDIA_AND_BACKUPS.md`, which the README never mentioned.

---

## 2026-09-20

### Fixed
- **Guest QR menu: ADD did nothing on some dishes, and extras could never be chosen** - `_build_modifier_data` returned an already-encoded JSON string and the template then ran it through `json_script`, which encodes a second time, so the page's `JSON.parse` produced a string instead of an object. `ITEM_MODIFIERS[itemId]` then read a single character out of that string: any dish whose id was smaller than the string's length threw `groups.forEach is not a function` and nothing was added to the cart, and the extras popup (sizes, toppings, required choices) could never appear for any dish. Present since the popup was added on 2026-06-03, and live in production because `deploy.sh` runs `origin/qsr`. The helper now returns a plain dict and `json_script` does the one and only encoding. Found while filming the guest ordering flow for the product walkthrough.

### Added
- **Tests for guest orders with extras** - 3 tests in `menu/tests.py` parse the embedded modifier data exactly as the browser does (they fail on the old code), and 4 tests in `orders/tests/test_qr_modifiers.py` cover the server side of a guest order that carries modifier ids, which nothing tested before because a browser could never send one: extras stored with their name and price and priced into the line and order subtotal, the price always taken from the database and never the request, an order with no extras still works, and another restaurant's extras are refused. Verified on SQLite (110 related tests pass) and with a real-browser run of the whole flow: popup opens, a required choice cannot be skipped, the price updates, and the order stores the chosen extras. Not run on Postgres locally, and there is no committed browser-level test.

---

## 2026-09-19

### Added
- **Point-in-time database recovery (WAL archiving)** - the nightly `pg_dump` alone meant a server failure at 1:59am lost up to 24 hours of orders. Postgres now ships every finished WAL segment to the private R2 bucket (gzip-compressed, under `wal/`) through `scripts/backup/archive_wal_to_r2.py`, and a weekly physical base backup (`scripts/backup/base_backup_to_r2.py`, under `base/`) is the anchor those segments replay onto. `scripts/backup/restore_wal_from_r2.py` fetches segments back during a recovery. WAL older than the oldest kept base backup (`R2_BASE_BACKUP_RETAIN_WEEKS`, default 4) is pruned inside the weekly job, so the trail stays bounded instead of growing forever. Verified with a real Docker drill (throwaway Postgres 16: real base backup, real archiving, real restore to a target time; rows committed before the target survived, rows after it did not), then set up on production (Postgres 18): `pg_stat_archiver` confirmed segments archiving and the first base backup (4.9 MB) landed in R2. There are no unit tests for these scripts, the drill and the live checks are the verification. The one-time server steps that are not in git (four `postgresql.conf` lines and a restart, the `REPLICATION` attribute on the app's DB role) are written up in `MEDIA_AND_BACKUPS.md` section 4b.
- **WAL archiving health check** - `scripts/backup/check_wal_archiving_health.py`, run every 15 minutes from cron as the `postgres` user. Three independent checks: Postgres's own `pg_stat_archiver` (last success recent, no failure newer than it), the actual `pg_wal` directory (a backlog of unarchived segments is what fills a small disk), and total R2 backup storage against `R2_STORAGE_WARN_GB` (default 8, under R2's 10GB free tier). The storage check only warns. It never deletes anything, because pruning WAL earlier than the stated retention window could silently strand an older base backup with nothing left to replay onto it.
- **`deploy.sh` now sets up the backup plumbing** - idempotent ACL grants so the `postgres` OS user can reach the app directory and write to `logs/` (without them `archive_command` fails on every segment with no visible error, because `/home/ubuntu` is `750` by default and `django.setup()` opens every log file), the three backup cron jobs installed once via a marker comment, and an informational WAL health check at the end of a deploy. It deliberately does not touch `postgresql.conf` or restart Postgres.

### Changed
- **All backup and recovery scripts now live in `scripts/backup/`** - the existing `backup_to_r2.py` and `restore_drill.py` moved there alongside the four new ones, and the path setup inside each was adjusted for the extra folder level. The production crontab still pointed at the old `scripts/backup_to_r2.py` path, which would have made the nightly backup fail silently after the move; it was corrected on the server and `deploy.sh` now installs the new path.

### Fixed
- **Nightly DB backup had a hidden memory and size ceiling** - `backup_to_r2.py` held the entire uncompressed dump in memory (`capture_output=True`) and then uploaded it with a single non-multipart `put_object`, which R2 caps at 5GiB. Fine at today's size, a silent failure once the database grows into it, and the memory limit on a `t3.micro` would have hit well before 5GiB. It now streams `pg_dump` into `gzip` into R2 with `upload_fileobj` (multipart automatically), so memory stays flat and there is no size ceiling. `base_backup_to_r2.py` had the same read-then-`put_object` pattern for its upload and now uses `upload_file`. The streaming path was verified against real R2 inside a Linux container, since a Windows-only subprocess pipe quirk made testing it directly on a Windows machine misleading.
- **WeasyPrint bumped from 69.0 to 70.0** - fixes Dependabot alert #38, a server-side request forgery in versions before 70.0 (medium severity).

## 2026-09-17

### Added
- **Inventory and 14-day sales history in the live demo** - the demo tenant previously only showed 2 sample in-progress orders, so Reports, Dashboard, and Inventory looked empty to anyone clicking past the order screen. `demo_seed.py` now also seeds 10 realistic ingredients (a few deliberately below their low-stock threshold, so that alert has something real to show) and backdates several paid orders per day across the last two weeks at realistic lunch/dinner hours, so a visitor sees an actual sales trend and payment-method breakdown instead of a flat, empty day. Runs on the same 2-hour reset schedule as everything else, with a fixed random seed so the generated history looks the same shape every time rather than reshuffling for no reason.

## 2026-09-07

### Added
- **Self-serve live demo** - replaces the old flow of sending a stranger a video or booking a call: `/live-demo/` is a rate-limited magic link that logs a visitor straight in as a demo owner account, no signup, no waiting on us. Backed by an idempotent seed (`orders/scripts/demo_seed.py`, `reset_demo_tenant` management command) that gives every visitor the same clean slate - a tenant called "Demo Bistro," 8 tables, a 17-item menu across 5 categories, and 2 sample in-progress orders - with AI menu import and real payment gateways left off by default so nobody can accidentally rack up a real bill or burn real API quota poking around. 13 new tests, including one that proves a locked-out login form doesn't block the separate magic-link path.
- **"Try Live Demo" CTAs on the marketing page** - the demo above had no way for an actual visitor to find it; now it's the primary button in the nav bar, the hero section, and the final call-to-action, with the old WhatsApp contact option kept as a secondary path.
- **Demo trailer mode** - the live demo above launched with zero restrictions, letting a visitor rewrite the curated menu, manage staff, or flip on settings (vendor emails, payment gateway) that don't reset on the existing schedule and would have quietly stayed on for every visitor after. A session flag now blocks menu editing, staff management, and outlet/payment settings while leaving the actual day-to-day flow (orders, kitchen, billing, tables, dashboard) fully open, plus a small dismissible banner suggesting what to try. `/live-demo/?key=<DEMO_FOUNDER_KEY>` skips the restriction entirely for doing a live walkthrough yourself, since the shared demo account has no usable password to log in with normally. The reset that used to require running a command by hand now also runs automatically every 2 hours. 15 new tests, full suite 1354/1354 passing.

## 2026-09-05

### Fixed
- **Split-bill QR accuracy** - the UPI "scan & pay" QR on the bill screen was drawn once at page load from the order's full remaining balance and never redrawn, so splitting a bill still asked the customer to pay everything, and the same stale QR printed onto the receipt. `renderUpiQR()` is now a real function, wired into both the payment-amount field and Split, so the QR always reflects the actual amount being collected.
- **Razorpay QR ignored splits entirely** - a second, worse version of the same problem: the Razorpay QR button never read the split amount at all and always requested the order's full balance from Razorpay, with no way to ask for less. It now accepts and validates a requested amount (bounded to the actual remaining balance) end to end - JS, view, and gateway call.
- **docker-compose.yml environment variable mismatch** - the `db` and `web` services read the database password from two different, cross-wired environment variable names, so a fresh container deploy would have connected with the wrong password. Both now read `DB_PASSWORD`, matching what the app itself expects everywhere else.
- **Blank gap at the top of the digital menu** - `fixStickyNav()` was copy-pasted from a different page with a `position:fixed` header. Here, both the header and category tabs are `position:sticky`, which already reserve their real height in the normal page flow, so the leftover code was adding that height again as margin, doubling it into a visible empty gap. Removed the redundant push; kept the parts still needed (positioning the category tabs below the header, and offsetting anchor-scroll targets).
- **`.gitignore` silently swallowing root markdown docs** - a blanket `*.md` rule, added in passing months ago before `md_files/` existed as the actual home for scratch markdown, was quietly ignoring any `.md` file that wasn't already tracked before the rule was added. Three real files had never once been tracked in git because of it: `CHANGELOG.md` itself, `docs/LOAD_TESTING.md` (which `README.md` had been linking to the whole time, pointing at a file that was never actually there), and `rasova_android/README.md`.

### Added
- **Order status for QR-ordering guests** - the order-status banner, which used to sit full-width under the header and push the whole menu down for as long as an order was active, is now a small floating pill (same idea as the existing cart bar) that opens a slide-up sheet with a Received → Preparing → Ready → Served timeline.
- **Reorder cart memory** - opening the cart to add a second round of items now shows a read-only "Already ordered" section above the new items, sourced from the same order-status data already being polled - the same idea as Swiggy/Zomato showing an earlier round when you add more to an in-progress order. Previously the cart only ever showed what was newly being added, with no memory of what had already been sent to the kitchen.
- **`celery_worker` and `celery_beat` services in docker-compose.yml** - the app has real scheduled tasks (`CELERY_BEAT_SCHEDULE` in `core/settings.py`) that had no way to run at all in a Dockerized deploy before this.
- 34 new tests: the split-bill QR fix (`orders/tests/test_bill_qr_amount_sync.py`, `payments/tests.py`), the order-status UI, the top-of-page gap regression, and the reorder-cart memory (all in `menu/tests.py`).

---

## Recent history

A condensed summary of the last two weeks of real, shipped work, grouped by theme rather than commit-by-commit. See `git log --since=2026-08-14` for the exact commits.

### 2026-09-04
- GSTR-1 export now includes Table 12 (HSN/SAC summary) - mandatory for every GST filer regardless of turnover, previously missing entirely.
- AI menu import now actually classifies veg/non-veg per item instead of silently defaulting everything to veg - fixed in both the Celery task path and the synchronous fallback path, plus the Gemini prompt and the manual/regex parser.
- Dark-mode dropdown text was invisible across the app, not just on the two element types first suspected - fixed everywhere the pattern occurred.
- The "Transfer Table" destination dropdown showed nothing with no explanation when no tables were free - now shows a clear fallback message.

### 2026-09-02 – 2026-09-03
- Fixed missing role checks on payment and setup endpoints, and a table-unmerge status bug.
- Fixed invisible white text in dropdown popups in dark mode (an earlier, narrower fix than the 09-04 one above).
- Added a pub-night simulation load test and a soak-test phase to `load_test`.

### 2026-08-26 – 2026-08-29
- Purchase orders: partial receiving, price variance capture, manual stock adjustment, draft editing, permission-gated vendor email.
- Fixed several real order/table-state bugs: adding an item after generating a bill silently splitting the order, sending to kitchen clobbering a billing table's state, a stale `order_id` wrongly blocking staff after a bill closes.
- Fixed duplicate and invisible notification badges, deduped low-stock alerts so one ongoing issue stopped looking like fifty, stopped the browser Back button from showing a stale authenticated page after logout.

---

*For anything before 2026-08-14, see the full commit history: `git log`.*
