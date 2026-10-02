# Changelog

All notable changes to Rasova are recorded here, newest first. This file starts
2026-09-05 — it is not a retroactive rewrite of the full project history.
For anything earlier than the "Recent history" section below, `git log` is
the source of truth.

---

## 2026-10-02: cleanup, one superuser panel

### Removed
- **The `/portal/` panel** (`portal/` app) - a second copy of the superuser panel at `/superuser/`. Both had been kept up in parallel, and `/superuser/` is the complete one: it also has subscription billing (plans, invoices, marking paid), which `/portal/` never got. `core/urls.py` tried to redirect `/superuser/` to `/portal/`, but `accounts/urls.py` registers `/superuser/` first, so those redirects never ran; they are gone. `robots.txt` now keeps crawlers off `/superuser/`. No database tables (the app had none).

- **Dead templates** - `orders/qsr_bill.html` (no view rendered it), `core/landing.html` (an old copy of the marketing page; `/` is `public/index.html`), `inventory/purchase_order.html` (its view only redirects now), `orders/templates/errors/404.html` and `500.html` (the handlers render `templates/404.html` and `500.html`).
- **Duplicate scripts** - `scripts/seed_restaurant.py` (a byte-for-byte copy of `orders/scripts/seed_restaurant.py`, which the `seed_restaurant` command uses) and `orders/scripts/simulate_restaurant_rush.py` (nothing imported it; the command has its own code).

- **64 unused imports** in 48 files, found by a scan and checked one by one: none is imported from those modules elsewhere or patched by a test. Two stay on purpose: `crm/models.py` imports `GuestFeedback` so Django registers the model (now marked so), and `core/gemma_service.py` is kept untouched as the start of a local-model fallback for the AI import.

### Moved
- **Documents into `docs/`** - `DEPLOY.md`, `ELI5_PRINTING_ARCHITECTURE.md`, `MEDIA_AND_BACKUPS.md`, `TESTING_STRATEGY.md`, `USER_MANUAL.md`, `parcel_charge_explainer.md`, and the tracked schema diagrams into `docs/diagrams/`. `README.md`, `CHANGELOG.md` and `PRINTING_SYSTEM.md` stay at the top. Links in the README, `deploy.sh` and the backup scripts follow.
- **Developer tools into a `devtools` app** - the 13 load-test, seeding and print-preview commands (`load_test`, `pub_night_test`, `http_rush_test`, `seed_restaurant`, ...) and the scripts they wrap move out of `orders`; they run by the same names. `reset_demo_tenant` stays in `orders` (the live demo uses it), and the live demo's seed moves from `orders/scripts/demo_seed.py` to `orders/services/demo_seed.py`, since it is product code. `orders/scripts/` is gone.
- **`.gitattributes`** - `.bat` files check out with Windows line endings (cmd.exe can misread an LF-only batch file, and the agent installer is downloaded and run as is), `.sh` files with Unix ones.

### Tests
- `accounts/test_superuser_panel.py` (4): the access checks that covered `/portal/` (superuser yes, owner 403, logged out to login) now cover `/superuser/`, and `/portal/` is a 404.
- `tenants/tests/test_tenant_config_service.py`: the tests comparing the two panels became tests of the one panel against the shared presets.
- CI runs the print agent's tests (`agent`), which it never did while they sat at the repository root.

## 2026-10-02: the print agent is downloaded from rasova.net

The Windows installer fetched `rasova_agent.py` from `raw.githubusercontent.com`, which only works while the GitHub repository is public. The repository was public, and it is being made private.

### Changed
- **The agent lives in `agent/`** (`rasova_agent.py`, `rasova_agent_installer.bat` and their tests) and rasova.net serves the two files at `/agent/rasova_agent.py` and `/agent/rasova_agent_installer.bat` (`core/views.py::agent_download`, no login, only those two names). The installer, the kitchen stations page and the printing setup sheet point there. The second copy of the installer in `static/agent/` is gone.
- **`CONTEXT_PROMPT.md` is out of the repository** (a working note; it is in `md_files/`, which git ignores). It held an out-of-date server IP and an e-mail address.
- `PRINTING_SYSTEM.md` describes the header, `--server`/`--key`, and the download.

### Tests
- `core/test_agent_download.py` (4): both files served without login, nothing else in the folder (including `../`), and the installer downloading from rasova.net, not GitHub. The agent's own tests run from `agent/` with the rest of the suite.

## 2026-10-02: the print agent's key leaves the URL

The phone and PC print agents poll for jobs every 2 seconds at `/orders/agent/<key>/jobs/`, with the outlet's secret key in the path, so the key was written into every nginx, proxy and Cloudflare log line.

### Changed
- **The key travels in the `X-Agent-Key` header** - the agent endpoints are now `/orders/agent/jobs/`, `/orders/agent/done/<id>/` and `/orders/agent/failed/<id>/`. The old paths with the key in them are gone (404). A missing, malformed or wrong key is a 403, as a wrong one always was.
- **Android app 1.1.0** (`rasova_android`, versionCode 2) - the page hands the app the server and the key separately (`Android.startPrintingWithKey`), and the app sends the key in the header. A phone that reboots before anyone logs in again keeps printing: a saved pre-1.1.0 poll URL is turned into the server and key once (`AgentConfig`). A refused key or a moved endpoint is now an error the notification shows; it used to read as "no jobs", so printing stopped silently. An app older than 1.1.0 can't print against this server; the page tells the user to update it. **Needs a signed release build from Android Studio, published as `public/android/rasova.apk`.**
- **PC agent 1.2.0** (`rasova_agent.py`) - `--server https://... --key <key>`; an agent set up with the old `--poll <url>` keeps working, its key taken out of the URL. The setup sheet shows the new command.

### Tests
- `printing/tests.py`: every agent call sends the header; new checks for a missing key, a malformed key, and the old key-in-path URLs answering 404. On the old routes 9 key tests fail.
- `test_rasova_agent.py` (4 new): reading `--server`/`--key` and the old `--poll` URL, and every poll carrying the key in a header and not the URL.
- `rasova_android/app/src/test/.../AgentConfigTest.kt` (3, the app's first unit tests): old poll URLs, non-URLs, the agent address without the key.

## 2026-10-02: guests on one Wi-Fi don't share a rate limit

Every public QR page was rate limited per IP, so all the guests behind a restaurant's router counted as one: three phones following their orders (each checks about 10 times a minute) used up the order-status page's 30 a minute between them, two tables ordering at once shared the 20 orders a minute, and two cashiers on the shop Wi-Fi shared it too.

### Fixed
- **Counted per guest** (`core/ratelimit_keys.py`) - a guest is counted by IP and the token they came with (their table's or the counter's QR token, or their order's status token), so each table has its own allowance: menu 30 a minute, order status 30, call waiter 10, orders 20. Staff placing orders are counted by their login, 60 a minute.
- **A looser cap per IP** on each page (menu and order status 300 a minute, orders 120, call waiter 60, which was 10 for the whole restaurant), so made-up tokens can't buy fresh allowances without end.
- The rate-limit tests hold django_ratelimit's clock still (`core/testing.py`), as the create-order test already did, so a slow run can't cross a minute window.

### Tests
- `core/test_guest_ratelimits.py` (5): two tables each opening the menu 30 times, three phones following their orders for a minute, two tables each placing 20 orders, two cashiers each placing 25, and made-up tokens still stopped by the per-IP cap. All five fail on the old limits.
- `menu/tests.py`: the call-waiter flood test now floods 60 tables, the new per-IP cap.

## 2026-10-02: errors that were swallowed, and the crash one of them hid

The code review counted six `except Exception: pass`. Each was looked at: one hid a real crash, three hid nothing they should, two were deliberate but silent.

### Fixed
- **A switched-off default kitchen station broke the bill page** - an outlet has one default station (the `one_default_station_per_outlet` constraint). `get_default_station` looked only for an active default and otherwise created a new one, which the constraint refused, on every call: the bill page failed, and the thermal print view hid it behind `except Exception: pass` and printed at a guessed width. Now the active default is used; if the default is switched off, the first active station stands in (or the switched-off default when nothing is active); with no default at all an active station becomes it, or a "General" one is made, safe when two requests do it at once. The print view no longer swallows anything there.
- **Deleting the default station failed** - it promoted the next station while the old one was still the default, which the same constraint refused. The old one now stops being the default first, in one transaction with the promotion.
- **The print queue's station lookup** (`printing/views.py::print_queue_add`) caught every exception around the same lookup and carried on with no station; it no longer needs to.
- **Three `order.token` checks** (thermal print view, order history detail and CSV) caught every exception to cope with an order that has no token. `getattr(order, "token", None)` does that without hiding anything else.
- **Two deliberate ones now log** - a cache (Redis) failure while queuing a print job still never blocks the job, and failing to record a printer error while a KOT print is already failing still doesn't stop the retry, but both are now warnings in the log instead of silence.

### Tests
- `setup/test_default_station.py` (8): the station lookup in each case, the bill page with a switched-off default, deleting the default station, and the logged cache failure. On the old code the bill page, the lookup and the delete fail with the constraint's IntegrityError, and nothing is logged.

## 2026-10-02: a QR guest's special instructions reach the kitchen

### Fixed
- **"Special instructions" were dropped** - the guest menu sent its instructions box ("less spicy") as `notes` with the cart, and `create_order` never read it, so the kitchen never saw it. It is now the kitchen note of each dish in that cart, shown on the KOT and the kitchen screen; a dish's own note wins. The box takes up to 200 characters, the length of any note.

### Tests
- `orders/tests/test_guest_instructions.py` (5): the instructions on every dish, a dish's own note winning, blank instructions leaving no note, too-long instructions refused, and the instructions printed on the KOT. 4 fail on the old code.

## 2026-10-02: UTGST only where the law charges it

### Fixed
- **Which bills say UTGST** - a bill names UTGST instead of SGST only in the union territories without a legislature (CGST Act, section 2(114)): Chandigarh, Ladakh, Lakshadweep, Andaman and Nicobar, and Dadra and Nagar Haveli and Daman and Diu. The list of GSTIN state codes had Delhi (07), Puducherry (34) and Andhra Pradesh (37, a state) and lacked Dadra and Nagar Haveli and Daman and Diu (26) and Other Territory (97), so an Andhra Pradesh or Delhi restaurant's bill said UTGST. The manual switch's help text and the outlet settings page said the same wrong thing and now list the right places (migration tenants 0038, help text only).

### Tests
- `tenants/tests/test_utgst.py` (5): every UTGST territory and seven states and territories with a legislature by GSTIN code, the manual switch without a GSTIN, a GSTIN winning over the switch, and the label on a real bill. 7 checks fail on the old list.

## 2026-10-02: Zomato and Swiggy orders carry no GST of their own (P22)

Since 1 January 2022 an e-commerce operator pays the GST on restaurant service supplied through it (CGST Act, section 9(5)): Zomato or Swiggy charges the guest GST and issues the tax invoice, and the restaurant reports the order's value in GSTR-1 Table 14. Rasova totalled those orders with GST at the dish's rate and counted them in B2CS, so a restaurant filing from the export paid GST the app had already paid: a ₹500 Zomato order was billed ₹525.

### Fixed
- **No GST on an app's order** - an order through Zomato, Swiggy or Uber Eats, whether it arrived by webhook or a cashier typed it in, is totalled under a fourth scheme, "operator": no GST on the dishes or the parcel charge, as on the composition scheme. A composition or unregistered outlet keeps its own scheme (no GST either way). Bills totalled before keep the GST they were billed with: each bill's tax record holds its scheme. The POS cart follows the source picker (`static/js/cart_tax.js`, `gstPaidByOperator`).
- **The bill says so** - headed "Bill", with "GST on this order is paid by Zomato (CGST Act, section 9(5))".
- **GSTR-1 Table 14** ("GSTR-1 Table 14 (App orders)") - the app orders' value by app, against each app's GSTIN, as "Liable to pay tax u/s 9(5)", with no tax; they are no longer in B2CS. The same value goes in GSTR-3B Table 3.1.1(ii), which the sheet notes.
- **Each app's GSTIN** in Aggregator settings (`setup.AggregatorConfig.zomato_gstin`, `swiggy_gstin`, `uber_eats_gstin`, migration setup 0012), shown whether or not the app's webhook is on, since a cashier can enter app orders by hand. An operator's tax-collection GSTIN (C where a GSTIN has Z) is accepted; anything else is refused with a message and the old one stays. Table 14 says where to add a missing one.

### Tests
- `orders/tests/test_operator_orders.py` (13): the engine's operator scheme, prices marked as including GST, composition and unregistered keeping their scheme; a webhook Zomato order and one typed in at the POS carry no GST while a takeaway does; the bill's wording; an app order from before the tax record keeps its GST; Table 14 has the orders and B2CS doesn't; a missing GSTIN; saving, cleaning and refusing GSTINs; and on random bills an app order costs exactly what the same bill costs on the composition scheme. Without the order-side change 6 of them fail, showing the ₹525 bill and ₹37.50 of GST in B2CS.
- `orders/tests/test_cart_tax_js.py`: 300 random app-order carts in Node match the engine to the paisa.

## 2026-10-02: GSTR-1 Table 8, nil rated and non-GST supplies (P20)

The GSTR-1 export put dishes sold at 0% GST in the B2CS sheet, as a 0% row. B2CS is for taxed supplies; nil rated supplies belong in Table 8. Liquor, which is outside GST altogether, was in no sheet at all.

### Fixed
- **A Table 8 sheet** ("GSTR-1 Table 8 (Nil, non-GST)") with the return's four rows. Every Rasova bill is to an unregistered guest in the outlet's own state, so the figures go on the intra-State, unregistered row: nil rated is the value of dishes sold at 0%, non-GST is liquor at its value before VAT (Karnataka has none). Exempted stays 0.
- **B2CS has only taxed rates** - the 0% row is gone; its tax totals are unchanged. Table 12 (the HSN summary) keeps its 0% row, as it covers every GST supply.

### Tests
- `reports/tests/test_tax_reports.py` (4 new): a 0% dish is nil rated and not in B2CS, Karnataka liquor is a non-GST supply, liquor priced with VAT counts at its value before VAT, and a period with neither is all zeros. All four fail on the old export.
- Golden reports: on the golden month the ₹1,481.33 0% row moved from B2CS to Table 8 and B2CS's taxable total fell by exactly that; nothing else moved.

## 2026-10-02: every bill adds up (P12, P13, P14, P15, P19)

A guest or an inspector adding up a Rasova bill could get a different total. The printed bill rounded every amount to the rupee (GST ₹125.28 printed as Rs.125) and showed one "GST" line; with prices including GST it printed the discount again after a subtotal that was already the value after discount (927 + 108 − 115 is not 1035); a free dish printed at full price; the WhatsApp bill had no discount or parcel line; and the 58 mm token receipt ran past the paper edge on a long dish name. Each of the five bills a guest can get worked out its own lines, so each had its own faults.

### Fixed
- **One layout for every bill** (`orders/services/bill_layout.py`) - the printed bill, the split-bill summary slip, the QSR token receipt, the thermal web bill, the A4 bill page and its PDF, and the WhatsApp bill all draw the same lines from the bill's tax record. Read top to bottom they add up: Subtotal (the dishes at menu prices), Discount (every discount, a dish's own and the bill's), CGST and SGST by rate when GST is added on top, VAT by rate likewise, the parcel charge, the round-off, TOTAL. Money is shown to the paisa everywhere.
- **Tax inside the prices is stated, not added again** - when prices include GST, the bill shows under the total each rate's taxable value and its CGST and SGST, as a tax invoice must (CGST Rules, rule 46), and the total included.
- **What kind of bill it is** - every bill is headed Tax Invoice, Bill of Supply or Bill, from the scheme it was totalled under. A composition outlet's bill carries "Composition taxable person, not eligible to collect tax on supplies" (P13), and no longer says its prices include GST. UTGST is named in place of SGST in a union territory, on every bill (it was only on the A4 page).
- **Free dishes** show FREE (or Free) at no cost on every bill, and count nothing in a counter slip's total.
- **Long dish names wrap** onto the next line on every printout instead of being cut off ("Kingfisher Pre") or, on the token receipt, running past the paper edge (P19).
- **The QSR token receipt is a proper bill** - at a counter it is the only bill the guest gets, and it showed only a total. It now carries its title, its bill number and the full money lines. The split-bill summary slip carries the bill number even when it shows a token.
- **The WhatsApp bill** shows the GSTIN, what kind of bill it is, the discount and the parcel charge.
- **A dish's own discount on the A4 page** - the page showed the dish at its reduced price, and the discount again in the Discount row. The dish now shows its menu price with "12.5% off" under it, and the Discount row carries it once.

### Tests
- `orders/tests/test_bill_layout.py`: on random bills in every outlet mode and on pub bills with liquor (Hypothesis, through the real `recalculate_totals()`), the rows add up to the total, the dishes to the subtotal, tax-extra GST appears as rows and tax-included GST only as included, a composition bill has neither; bills from before the tax record add up too; worked examples (the 1035 bill, CGST and SGST by rate, parcel and round-off, composition, no GSTIN). And the bills as a guest gets them: every golden bill, printed at 58 and 80 mm and as the WhatsApp page, read back and added up. On the old renderers that test fails.
- Golden receipts and HTML bills regenerated: every money line moved, as intended; the diff was read.
- `orders/tests/test_gst_registration.py`: the CGST wording checks follow the new label ("CGST 2.5%").

## 2026-10-01: security fixes from the code review

An outside review of the code listed holes in guest ordering, the aggregator webhook and the client IP. Each was checked against the code before it was fixed.

### Fixed
- **A QR guest can no longer say where an order came from** - `create_order` saved the `source` and `aggregator_id` the caller sent, guests included. A guest could label a QR order "zomato", or claim a real Zomato order's ID; that ID is unique per outlet, so Zomato's own webhook for it would then be turned away as a duplicate. A guest now always places a website order with no aggregator ID (the menu page always sent "web", so nothing changes for real guests). Staff values must be real choices, which Django doesn't enforce on save: an unknown source is refused, an aggregator ID is kept only for Zomato, Swiggy, Uber Eats and Website orders (the ones the billing screen shows the box for), and an over-long ID is a clear error instead of a failed save.
- **The order history "QR Menu" filter found nothing** - it looked for orders from "qr_menu", but QR orders are saved as "web". The filter now lists exactly the sources orders are saved with (adding Counter and Uber Eats, which were missing), and shows "Dine In" where it showed "Dinein".

- **A caller can no longer choose its own IP** - `get_client_ip` believed `CF-Connecting-IP` whenever it was there, then the first hop of `X-Forwarded-For`. Anyone reaching the server directly, around Cloudflare, could send either and be any IP: a fresh one per request to escape the order and login rate limits and login lockouts, or 127.0.0.1 to pass the aggregator webhook's IP allowlist (its signature check still stood). Now nginx's `X-Real-IP` is believed only from nginx itself, `CF-Connecting-IP` only when that address is one of Cloudflare's published ranges, and `X-Forwarded-For` never. Login lockouts (axes) use the same function instead of their own header list. No server change needed: nginx already sets `X-Real-IP`.

- **The aggregator webhook** (`orders/api.py::api_ingest_order`), checked against the old view with its own signature:
  - *A repeated delivery answered 400*, and Zomato and Swiggy retry until they get a 2xx. It now answers 200 with the order already made (`"duplicate": true`), also when two deliveries arrive at once, also after a dish on it was since removed.
  - *An order with no order ID was never deduplicated* (the unique constraint ignores a blank one), so two deliveries made two paid orders. Every webhook order must now carry its ID (text or a number, up to 100 characters).
  - *Quantity wasn't checked*: 0 made an order for zero portions, "abc" a 500. It must be a whole number from 1 to 999; an order has at most 100 lines (`orders/services/cart_limits.py`).
  - *The tenant and outlet were looked up before the signature*, and a wrong ID was a 500 with a logged traceback, so anyone could fill the error log. Only the outlet's webhook settings are read before the signature check, with a lookup that can't raise; an unknown tenant, outlet or secret and a bad signature all get the same 401, so the endpoint can't be used to find which IDs exist. A body that isn't a JSON object, or items that aren't a list of objects, is a 400, not a 500.
  - *A signed request could be replayed at any time.* The caller now signs `"<X-Timestamp>.<body>"` and a timestamp more than 5 minutes from now is refused (`AGGREGATOR_WEBHOOK_MAX_AGE_SECONDS`); a replay inside the window is the same order ID, so it answers with the existing order. This changes what callers sign; nothing outside Rasova calls the endpoint yet, and the view, `scripts/simulate_aggregator_order.py` and the tests all sign through `orders/services/aggregator_webhook.py`.
  - *The IP allowlist* now takes ranges (CIDR) as well as single addresses, ignores stray spaces, and logs the real caller's IP when it turns one away (it logged nginx's 127.0.0.1).
- **Limits on what one order request can carry** (`orders/services/cart_limits.py`) - quantity had a floor but no ceiling, and nothing bounded the number of lines, a note or a modifier list. A QR guest can now order up to 50 of a dish and 30 lines a request; staff up to 999 and 100 lines; a kitchen note is up to 200 characters and a dish up to 20 modifiers, for everyone. 2.5 portions is refused instead of quietly becoming 2, "takeaway" counts only when it is a real true, and a cart, item or modifier list of the wrong shape is a clear message instead of "Could not create the order". Every cart message now reaches the person ordering ("'Masala Dosa' is currently unavailable." was hidden behind that same generic error), and the guest menu shows it.
- **The Razorpay webhook crashed on junk** - the outlet is looked up before the signature can be checked, and `tenant_id=abc` in the URL raised inside that lookup: a logged 500 anyone could trigger. A payload of the wrong shape (a list, a `qr_code` of null) and a payment amount that wasn't a whole number crashed it too, and Razorpay retries a failed delivery for a day. IDs and the amount must now be positive whole numbers (`core.validators.positive_int`, also used by the aggregator webhook), and anything else is a 400.

### Tests
- `orders/tests/test_cart_limits.py` (14): the guest and staff quantity and line limits at and past the edge, a long note, a 200-character note kept, too many modifiers, modifiers that aren't a list or aren't IDs, fractional and boolean quantities, a cart that isn't a list, a real modifier still priced, takeaway only on a real true, and an unavailable dish's own message reaching the guest. 12 of the 14 fail on the old code.
- `payments/tests.py` (3 new): junk IDs in the webhook URL, eight payloads of the wrong shape, and a closed QR without its entity. All three crashed the old handler.
- `orders/tests/test_aggregator_ingest.py` (26, was 6): retried and simultaneous deliveries, a missing or over-long ID, a numeric ID, eight bad quantities and three good ones, bodies that used to crash, too many lines, unknown and cross-tenant IDs answered 401 with nothing logged, a wrong secret, unknown sources, requests older or newer than 5 minutes, a moved timestamp, the old body-only signature, missing timestamps, and allowlist ranges. The two duplicate tests that expected 400 now expect 200. The liquor and online-token webhook tests sign the new way.
- `core/test_client_ip.py` (12): a visitor through Cloudflare (IPv4 and IPv6), a direct caller claiming 127.0.0.1, `X-Forwarded-For` ignored, `X-Real-IP` only from nginx, a bad Cloudflare header, local development, axes getting the same answer, a direct caller rotating fake Cloudflare headers still hitting the 20-a-minute limit, two visitors behind Cloudflare keeping their own limits, and the allowlist turning away a direct caller claiming 127.0.0.1. 6 of the 12 fail on the old code.
- `orders/tests/test_order_source.py` (11): a table guest and a counter guest claiming Zomato or Swiggy place website orders, a guest can't take a real aggregator ID, a guest's nonsense source doesn't break the menu, staff unknown and non-text sources are refused with nothing saved, a Zomato order keeps its ID, a takeaway drops one, an over-long ID is refused, and the history filter offers every saved source and no "qr_menu". 10 of the 11 fail on the old code.

## 2026-09-29: GSTR-1 Table 13, documents issued (P23, P24)

Table 13 of the GST return, documents issued, can't be left blank since the May 2025 returns. Rasova's export had no such sheet, so the CA counted bill numbers by hand.

### Added
- **A Table 13 sheet in the GSTR-1 workbook** - one row per bill number series in the period ("Invoices for outward supply"): the first and last number, how many bills, how many were cancelled, and how many stand (`reports/services/documents_issued.py`). A bill cancelled after it was billed counts as cancelled; an order cancelled before its bill never had a number and isn't a document. Bills count in the period of their business day, as in every report. Total counts the bills, so a bill opened at the end of one period and billed after the next began never inflates the next period's count. Old-style numbers (INV-...) make one series a day, as they were.

### Changed
- **Table 12 is labelled the B2C tab** (P24) - since the May 2025 returns Table 12 has a B2B and a B2C tab; Rasova issues only B2C bills, and the sheet and its title now say so.

### Tests
- `reports/tests/test_documents_issued.py` (6): splitting bill numbers, series rows, cancelled bills, a bill from the next period, old daily series, and the real workbook with two outlets (paid, cancelled after billing, cancelled before billing, shown but unpaid).
- Golden reports: the Table 13 sheet added (on the golden month, 100 bills in three series, the same 100 bills daily sales counts) and Table 12 renamed; nothing else moved.

## 2026-09-29: every report counts the business day (P16)

A restaurant open past midnight sells at 12:30 AM on the day still trading. Daily sales and the GST return counted business days (6 AM to 6 AM), but the profit and comparison reports, the stock consumption and variance reports, the daily chart, CRM trends and order history counted calendar days, so they put those sales on the next day. The same month showed one total in the P&L and another in daily sales.

### Fixed
- **One day, one meaning** - every report counts business days through two helpers in `core/utils.py`: `get_business_period()` for a range of days and `business_date_of()` for one bar per day. On the golden month the P&L now matches daily sales exactly in every outlet: 100 bills and ₹87,455, where it counted 99 bills and ₹89,602.
- **Stock reports after midnight** - consumption and variance for a day include the sales and stock movements of its night, and "today" means the day still trading.
- **Order history** - a waiter's "today", the cashier's and captain's windows and the date filters count business days, so a waiter at 1 AM still sees the evening's bills.
- **Tests that failed between midnight and 6 AM** - tests of the stock, profit, finance and CRM reports asked for the calendar day while the reports count business days; five of them failed between midnight and 6 AM even before this change, so CI failed for any push in those hours and nothing deployed. They all ask for the business day now, and the stock tests' helpers no longer pass the calendar date to work around the old mix-up. Run at 2 AM, they pass.

### Tests
- `reports/tests/test_business_day.py` (4): standing at 1 AM with sales at 10 PM, 12:30 AM and 7 AM, the P&L, comparison and daily sales agree, the daily chart, the stock reports and a waiter's history put the night on its business day. Putting back any one report's calendar-day version fails them.
- Golden reports: gross margin, net profit, period comparison, the P&L sheet and the several-day chart moved to the business-day figures, now equal to daily sales; nothing else moved.

## 2026-09-29: bill numbers within the law (P18)

A tax invoice's number may have at most 16 characters and must be unique in its financial year (CGST Rules, rule 46(b)). Rasova's had 19 or more (`INV-6-20260928-0001`), in a new series every day, and every order took one when it was opened, so orders cancelled before their bill left gaps.

### Changed
- **Bills are numbered like SG/2627/000123** - one series per outlet per financial year (April to March): the outlet's code, the year, the bill's place in the series. At most 16 characters, for up to 9,999,999 bills an outlet a year (`orders/services/bill_numbers.py`).
- **A number is given when the order is billed** - the Bill button, a payment, or an order that arrives paid; never when it is opened. An order cancelled before its bill leaves no gap, and a bill cancelled after keeps its number. Two screens billing at once still give one number, and a screen holding an older copy can't wipe it. The year is the one of the order's business day, as in the reports.
- **Every bill shows it** - the bill page, the thermal and printed receipts, the WhatsApp bill and message, the QSR bill, order history (searchable), refunds and loyalty entries, and a new "Bill No" column in the orders CSV. Before an order is billed, screens show its order number as before; the kitchen keeps the order number.
- **Old bills keep their numbers** - bills already presented or paid keep the number they were printed with (copied into the new `Order.bill_number`). Orders still open when this goes live get a new number when billed.

### Added
- **A bill number code for each outlet** - up to 3 letters or digits, set from the restaurant's name ("Spice Garden" -> SG, a second outlet SG2), editable in Outlet Settings, which shows what the next number looks like. A code that isn't one, or that another outlet uses, is refused with the reason; a new code starts a new series. Every existing outlet gets one when this goes live.
- `BillSeries` keeps each series and its last number, for the Table 13 work (P23). The admin lists and searches bill numbers, and the demo's series start again with each reset.

### Tests
- `orders/tests/test_bill_numbers.py` (22): the year and code rules; numbering at billing with no gaps, per outlet and per year; stale copies; screens billing at once on real row locks (eight bills together, and one bill on two screens); both migrations run on rows as they are in production; the settings, the printouts and the demo reset. Breaking each rule on purpose (numbering at opening, no re-read under the lock, stale saves, the year from today, shared codes) fails them.
- Golden receipts: the 44 bill number lines changed from their test labels to GB/2627/000001 and on, nothing else. Golden reports: the orders CSV gains its "Bill No" column, nothing else. Both golden builders now bill an order after giving it its fixed date, so the goldens never depend on today's date.

## 2026-09-28 (night): every GSTR-1 column readable (P26)

### Fixed
- **Cut-off headers in the GSTR-1 workbook** - the column sizing found each column from its top cell, which under the merged title row is a merged cell, so it skipped every column but the first and sized that one to the title: "Type" came out huge while "Place of Supply", "Central Tax (CGST)" and the rest were cut off, on both the B2CS and the Table 12 sheet. Each column is now sized to its longest header or figure, and a title merged across columns sizes none of them. `reports/tests/test_tax_reports.py` checks that every cell on both sheets fits its column (it fails on the old sizing), and both sheets were checked in Excel itself.

## 2026-09-28 (night): the demo gets its GSTIN at deploy

### Fixed
- **No window without GST on the live demo** - an outlet without a GSTIN charges no GST (P25), and the demo reset puts the demo's sample GSTIN back only every two hours. A data migration (`tenants` 0036) sets it during the deploy. It does nothing where there is no demo restaurant.

## 2026-09-28 (night): no GSTIN, no GST (P25)

Stage 2 starts. Only a business registered for GST may collect it (CGST Act, section 32), and a tax invoice must show the seller's GSTIN (CGST Rules, rule 46). Until now an outlet with no GSTIN charged GST anyway and printed "Tax Invoice" without one. Outlets with a GSTIN bill exactly as before: every golden bill, receipt, HTML bill and report is unchanged.

### Changed
- **An outlet without a valid GSTIN bills without GST** - on the dishes and on the parcel charge, as on the composition scheme. The tax engine takes `gst_registered` (`Outlet.is_gst_registered`: a GSTIN in the right format); liquor VAT is not GST and stays. The three carts follow (`cart_tax.js` takes `gstRegistered`) and hide their GST line.
- **Each bill records its GST scheme** - the tax record keeps `scheme`: "regular" (a tax invoice), "composition" (a bill of supply) or "unregistered". The bill page, the thermal receipt and the printer word the bill from it, so an issued bill reads as it was issued whatever the outlet changes later. An unregistered outlet's bill is titled "Bill", and the thermal receipt no longer prints a "GST Rs.0" line on it.
- **Bills from before this keep their GST** - a bill with no tax record is worked out as it was billed (every outlet charged GST then), so no report of a past month moves.

### Added
- **A GSTIN that isn't one is never saved** - Outlet Settings, the onboarding wizard, and the superuser and agency pages (new restaurant and outlet settings) check it and say why ("12345 is not a valid GSTIN, so it was not saved."), keeping the old one (`tenants.models.read_gstin`). A blank GSTIN is stored as none.
- **The owner is told** - Outlet Settings says "No GSTIN, so bills carry no GST" under the GSTIN box and warns on save, the GST Rates page carries a banner, and the superuser and agency pages show the same note.
- **The demo restaurant keeps a sample GSTIN** (`SAMPLE_GSTIN`, made up), put back on every 2-hour reset, so it still shows GST bills. The local load, rush and POS setup commands use it too.

### Tests
- `orders/tests/test_gst_registration.py` (18): bills, the bill page and receipt, the GST Rates and settings pages, all five places a GSTIN is saved, old bills, an issued bill after the GSTIN goes, the demo reset. Breaking each part of the rule on purpose (every outlet registered, old bills re-totalled, wording from today's outlet, any GSTIN saved) fails them.
- Engine: `UnregisteredTest` (4) in `test_tax_engine.py`. Random real bills in `test_totals_properties.py`: no GSTIN costs exactly what the composition scheme costs. Carts: 3 hand-worked and 300 random carts in Node against the engine, and the wiring on all three pages.
- Test outlets that bill GST now carry a GSTIN, as real ones do.

## 2026-09-28 (night): the tenants app's tests run

### Fixed
- **72 tests that never ran** - `tenants/tests` had no `__init__.py`, so test discovery skipped it and CI's `tenants` label ran nothing: tenant isolation, the settings service, the GSTIN and FSSAI validators and the suspension middleware went untested. They run now. One had gone stale unnoticed: the setup page sends anyone who isn't an owner or manager to the dashboard, so the suspension test's superuser now sits in the owner's seat, where only the suspension middleware could stop them.

## 2026-09-28 (later): liquor on the bill, behind a switch

Phase 1 of the liquor VAT plan. Nothing changes for any restaurant until a superuser turns on the new `liquor_vat` feature for it; with it off, every line is GST exactly as before, and all 5,000 golden bills are unchanged.

### Added
- **Liquor classes** - `menu.VatClass`: the state's VAT rate for one kind of liquor at one outlet ("Beer 0%"). A drink points at its class (`MenuItem.vat_class`) and carries no GST, which the database enforces. A 0% class is valid: that liquor is still a non-GST supply, kept apart from nil-rated food.
- **Karnataka starts at 0%** - Karnataka has charged no VAT on liquor at the bar since July 2017 (its 5.5% ran from March 2014; the state now takes its share as excise, by alcohol content since 11 May 2026). `menu/liquor.py` records the rate for the states we have checked, Karnataka so far, and a new liquor class at a Karnataka outlet starts at 0%. Elsewhere the owner has to give the rate: states differ.
- **Every bill line remembers its tax** - `OrderItem.tax_kind` ("gst" or "vat"), `vat_rate` and `vat_class_name` are copied when the line is ordered: at the POS, from the QR menu, from Swiggy/Zomato, and onto the split line when a quantity is reduced. Changing a class's rate never changes a bill already made.
- **Food and liquor on one bill** - the tax engine totals liquor as a second kind of tax. VAT never enters CGST or SGST, each kind's tax is rounded once, the order discount is spread over food and drinks by value, and food and drinks can each be priced with or without their tax (new outlet fields `vat_inclusive`, `vat_registration_no`, `liquor_billing_mode`). `Order.vat_total` and the tax record's new sections keep the two apart. The plan's sample bill comes out as worked by hand: ₹1,948 (round-off -₹0.45) at 5.5%, ₹1,754 with 10% off, and ₹1,883 with no liquor tax in Karnataka.
- **The carts know each dish's tax** - the POS, the QSR counter and the QR menu get a table of every dish's tax from the same rule the bill uses (`sale_tax_map`, two queries however many dishes), and show a VAT line only when there is VAT.
- **The switch on the superuser feature page** - "Liquor (State VAT)", in Ordering & Billing.

### Rules enforced
- **No liquor on the composition scheme (D7)** - the law bars composition for anyone selling something outside GST. A liquor class can't be created, and a drink can't be classified, at an outlet on composition. Outlet Settings and the superuser portal refuse to turn composition on while an outlet sells liquor, and say why (the two portal pages now show such messages).
- **Swiggy and Zomato orders with liquor are refused whole** (422), before anything is written.
- **The menu sync** gives a drink the target outlet's class of the same name; without one, the drink arrives unavailable instead of being sold under a guessed tax.
- **The GST Rates page** never gives a drink a GST rate, and moving a whole category leaves its drinks alone.

### Tests
- `orders/tests/test_liquor_vat.py` (23): line snapshots, the feature switch, the bill's sections, Swiggy/Zomato, the menu sync, the GST Rates page, D7 on every path, Karnataka's default.
- Nine hand-worked liquor bills in `test_tax_engine.py`; liquor carts in `test_cart_wiring.py` (4) and `test_cart_tax_js.py` (400 random pub carts in Node against the real engine, and the dish-tax lookup).
- **1,000 golden pub bills** - `orders/tests/test_golden_liquor.py` + `golden/liquor_totals_v1.jsonl`, every mode of food and drink pricing, liquor at 0%, 5.5%, 10% and 20%. An independent implementation of the liquor rules (`legacy_totals.liquor_totals`, written from the rules, not the engine's code) agrees with all 1,000; the frozen food maths is untouched.
- Seven rules for pub bills in `test_totals_properties.py`, and random pub bills through the real `recalculate_totals()` against the independent copy.

### Upgrade notes
- Migrations: `menu 0016` (VatClass, MenuItem.vat_class, the no-GST-on-liquor rule), `orders 0063` (each line's tax kind and VAT, Order.vat_total), `tenants 0035` (the outlet's liquor settings, and a database default for `parcel_gst_rate`). The new columns carry database defaults, so the previous release keeps writing orders while the deploy runs.
- The feature is custom-only: it is off everywhere until turned on for a tenant.

---

## 2026-09-28: a paid bill stays paid, whatever another screen does

### Fixed
- **Adding items could re-open a paid bill** - the add-items request (`create_order`) read the order without a lock and later saved the whole row back. A payment landing in between, on another screen, was undone: the bill went back to open and the new items went onto it. The request now locks the order before changing it, checks its status under the lock, saves only the fields it changes, and says the order was just paid (409).
- **The parcel toggle could re-total a paid bill** - the same race: it now locks the order for the whole change, and a bill paid meanwhile is refused (404) and left as it was.
- **The model checks the database, not only its own copy** - `Order.recalculate_totals()` refuses an issued bill when either the copy in hand or the database says it is paid or closed, reading the row under a lock inside a transaction. `IssuedBillError` moved to `orders/exceptions.py` and is now an `OrderError` with a plain message ("This bill is already paid, so it can't be changed. Correct it with a refund."), so screens that handle order errors show it instead of an error page.
- **Item discount and "make complimentary" on a locked bill** answered with a server error; they now give the reason (400).

### Tests
- `orders/tests/test_issued_bill_guard.py` (10). Its two race tests hold the order's row lock (as a payment does), start the request, take the payment, then let the request go on: the same interleaving every time, whichever thread runs first. Against the old views both failed (the paid bill was re-opened with the new dish on it; the parcel charge was written onto the paid bill). With the fixes both pass.
- Every place that re-totals a bill was checked: voids, quantity changes, cancelling, adding items, order and item discounts, complimentary dishes, generating the bill, the parcel toggle, Swiggy/Zomato imports, the demo seed and the test commands. The others already locked the order and checked its status.

---

## 2026-09-27 (evening): one tax engine for every bill, and today's GST problems fixed

Phase 0 of the liquor VAT plan. Every bill without a taxed parcel charge still totals exactly as before: all 5,000 golden bills were checked line by line against the version committed that morning.

### Changed
- **One tax engine** - `orders/services/tax_engine.py` is now the only place Rasova does tax maths, and its docstring states every rule. `Order.recalculate_totals()` runs it and stores the bill's **tax record** in the new `Order.tax_summary`: the taxable value, tax, CGST and SGST at each rate (0% included), and the parcel charge's GST. The bill page's breakdown, GSTR-1 and the tax inspection screen read that record; nothing re-does the maths any more. Every line carries a tax kind, so liquor VAT will be a second kind of tax, not another rewrite. Bills totalled before the record existed are worked out by the same engine when a report needs them, which gives exactly the totals they were billed with. `gst_breakdown_cache` is still written in its old shape next to the record, so a rollback to the previous release still shows every breakdown.
- **A paid or closed bill is never re-totalled** - `recalculate_totals()` refuses it with `IssuedBillError`. Every screen already stopped before that point; the model now makes sure nothing new can get past it. An order that arrives already paid (aggregators) is totalled once.

### Fixed
- **The parcel charge carried no GST (P5)** - packing is part of the restaurant service and carries the food's rate. New outlet setting "GST on the Parcel Charge" (Outlet Settings and the superuser portal; 0%, 5% or 18%, default 5%). `toggle_parcel` copies the rate onto the bill when the charge is turned on, so changing the setting later never changes a bill already made. Prices excluding GST: the GST is added on top (a ₹10 parcel becomes ₹10.50). Prices including GST: it is inside the charge and the guest pays the same. None on the composition scheme. Parcel orders from before today have no rate and stay untaxed. In the golden bills this changed 661 parcel bills, each by exactly the parcel's own GST.
- **The tax breakdown didn't add up to the bill (P11)** - each dish's tax was rounded on its own for the breakdown, while the bill's GST was rounded once. On 519 of the 5,000 golden bills the rows missed the GST by a paisa or two, and one bill charged ₹0.01 GST with no row showing it. The engine keeps the one rounding (so totals don't move) and allocates the rows to it, largest remainder first; CGST and SGST per rate are allocated the same way. The rows now always add up to the bill's GST, CGST and SGST. No row moved by more than 2 paise.
- **Composition bills stored GST rows (P7)** - a bill of supply now has none (2,138 golden bills).
- **GSTR-1 used each dish's current menu rate (P2), left out parcel charges and could drift from the bills (P17)** - it now sums the bills' own tax records: each dish at the rate it was sold at, parcel charges included, and its total tax equals the bills' GST to the paisa.
- **The tax inspection screen (P9)** - "Taxable Value" showed the tax, and "Total GST" dropped the paise (₹12.75 + ₹12.75 showed as ₹24). It now shows the taxable value, CGST, SGST and GST at every rate, from the same records as GSTR-1, with paise.
- **Times in UTC (P8)** - the receipt the phone agent prints and the orders CSV now show Indian time: a bill made at 8 PM printed 14:30.
- **Carts guessed GST (P3, P10)** - the QR menu said "GST (5%)" for everything, turned a 0% dish into 5% and added GST on top at GST-included and composition outlets; the POS cart did the same, and the QSR cart missed composition. All three now use one script, `static/js/cart_tax.js`, the browser copy of the engine, which follows the same rules to the paisa, parcel GST included.
- **GST rate list (P1)** - the GST Rates page offered "18%, AC / Liquor License", 12% and 28%. It now offers 0%, 5% and 18% with honest labels, says alcohol is not under GST, refuses 12% and 28%, and marks any dish still on a retired rate "old rate" (it used to show as 0%).
- **Bill lines in random order (P19)** - a bill's lines came back in whatever order the database stored them. `OrderItem` now always lists them in the order they were added (bills, receipts, KOTs, screens).
- **Reports and exports with ties** - top items, category sales, the payment split and the items, category and waiter CSVs sorted only by a value that could tie, and the orders CSV listed a bill's payment methods in database order, so the same month could export in a different order each time. Each now has a fixed tie-break.

### Tests
- New: `orders/tests/test_tax_engine.py` (23 rules, hand-worked), `test_tax_record.py` (22: the stored record, issued bills, parcel GST through the real toggle, the settings), `reports/tests/test_tax_reports.py` (10: GSTR-1, the queries it makes, inspection, Indian time), `menu/test_gst_rates.py` (5), `orders/tests/test_cart_wiring.py` (6), and `orders/tests/test_cart_tax_js.py`, which runs the cart script in Node on 400 random carts against the real engine (GitHub's runners have Node; in CI a missing Node fails the test instead of skipping it).
- The property tests now require the rows to add up exactly, and add the parcel rules. `legacy_totals.py` became a second, independent implementation of the engine's rules; it agrees with all 5,000 golden bills.
- Golden files regenerated and reviewed: every changed line is one of the changes above.
- Parcel tests in `test_parcel_dispatch.py` and `test_billing_modes.py` now expect the parcel's GST, with the arithmetic written out.
- **Repo scanners read only the project's own files** - three checks in `core/tests.py` (multi-line template comments, `.unscoped()` call sites, header links) walked every folder, so the local lab copies of the app in `md_files/` made them fail on a laptop while passing in CI. They now read git's list of the project's files (tracked, plus new files not yet added, minus anything ignored), and `AppFilesTest` pins what they read.
- **Full suite**: 1,543 tests; the only two failures were those scanners, fixed as above. A deep run of the property rules (220,000 random bills) and of 3,000 real bills against the independent copy passed.

### Upgrade notes
- Migrations: `tenants 0034` (Outlet.parcel_gst_rate, default 5%), `orders 0061` (Order.parcel_gst_rate and Order.tax_summary, both nullable: no table rewrite), `orders 0062` (line order, no SQL).
- No stored bill is rewritten. Bills from before today keep their totals and breakdowns.

---

## 2026-09-27 (later): a safety net for the bill maths

The liquor VAT work will rewrite how bills are totalled, printed and reported. Before any of that, these tests pin exactly what Rasova does today, so every change it makes is either deliberate and reviewed, or caught.

### Tests
- **5,000 golden bills** - `orders/tests/test_golden_totals.py`: fixed bills in every outlet mode (GST extra, GST included, composition, composition with GST included), with free dishes, one-paisa items, 100% discounts, discounts bigger than the bill, parcel charges and all five GST rates, are totalled by the real `recalculate_totals()` and must match `orders/tests/golden/totals_v1.jsonl` to the paisa. One line per bill, so a deliberate change shows in `git diff` as exactly the bills it touched. A second test checks that totalling a bill twice changes nothing.
- **A frozen copy of today's maths** - `orders/tests/legacy_totals.py` totals a bill the way `orders/models.py` does on 27 September, as a plain function, and is checked against all 5,000 golden bills. New code gets compared with it, and it may only change in the same commit as a deliberate change to the maths.
- **Property tests** (Hypothesis) - `orders/tests/test_totals_properties.py`: nine rules, each checked on 500 random bills. The bill adds up; the grand total is whole rupees with at most 50 paise round-off; CGST and SGST split the GST evenly; composition outlets charge no GST; voided and free dishes change nothing; a GST-included menu price is what the guest pays; a discount never goes past the bill; the rate breakdown is well formed and stays within half a paisa per dish of the GST total. A tenth test puts 150 random bills through the real `recalculate_totals()` and compares each with the frozen copy. On a mismatch, Hypothesis shrinks it to the smallest bill that shows it. CI uses the same random bills on every run, so a red build always means the code changed. A deep local run (`MONEY_THOROUGH=1`: 20,000 bills per rule and 3,000 real ones) passed.
- **Golden receipts** - `orders/tests/test_golden_receipts.py`: five bills in each outlet mode. Each is printed three ways: as the real bytes the phone agent sends to the printer (58 mm and 80 mm paper), as the split-bill slips and as the QSR token receipt. The output is decoded into readable lines, with font, bold, alignment and exact width, in `orders/tests/golden/receipts_v1.txt`. `orders/tests/test_golden_html_bills.py` does the same for the three web bills (the bill page, which the PDF also renders; the thermal HTML receipt; the WhatsApp link). It keeps only the lines that carry money or tax wording, so layout changes elsewhere on those pages don't disturb it.
- **Golden reports** - `reports/tests/test_golden_reports.py` seeds a fixed September at three outlets (`reports/tests/golden_month.py`). The month includes bills after midnight at the month's edges, cancelled and unpaid orders, voided and free dishes, split payments and a refund. It pins daily and hourly sales, category and item sales, gross margin, net profit, period comparison, the orders, items, category and P&L CSVs, and every cell of the GSTR-1 workbook in `reports/tests/golden/reports_v1.json`.
- **Test-only packages** - `requirements-test.txt` (hypothesis, coverage), installed by CI and included from `requirements-dev.txt`. Production installs (`requirements.txt`, `deploy.sh`, the Dockerfile) are unchanged.
- The golden files pin today's behaviour as it is, known problems included: bill times printed in UTC, whole-rupee amounts and no CGST/SGST lines on the printed bill, composition bills without "Bill of Supply", a WhatsApp bill with no discount line, GSTR-1 using today's menu rate and leaving out parcel charges, and reports that count the same month differently. They are listed with their fixes in `md_files/liquor_vat_food_gst_plan_2026-09-27.html`. Each fix will arrive as a reviewed change to a golden file.

No app code changed.

---

## 2026-09-27

### Fixed
- **The kitchen display got slower every day** - its data call (`get_kitchen_data` in `kitchen/services/kitchen_service.py`) loaded every KOT the outlet had ever had, then asked the database separately for each one's unfinished dishes (`kot.items.exclude(...)` skips the prefetch) and again for each dish's menu name. The screen asks every 5 seconds. A load test on a t3.micro-sized copy of production measured about 1.1 ms per past KOT: 1,000 KOTs made one poll take about 1 second, 10,000 about 12 seconds, and a single open kitchen screen at 1,000 KOTs used more CPU than a t3.micro's whole all-day allowance. The database now returns only KOTs that still have an unfinished dish, with those dishes and their menu items fetched in one more query, so a poll is the same small number of queries however long the history is. What the screen shows is unchanged: same KOTs, same dishes, cancelled orders and finished dishes still hidden, the station filter and the token pickup rule as before. Dishes within a ticket are now always listed in the order they were added.

### Tests
- **3 new tests** in `kitchen/tests.py` (`KitchenDataQueryCountTest`): a poll makes the same number of queries before and after 25 finished KOTs are added (the old code went from 10 to 35), a ticket with a served and an unserved dish lists only the unserved one, and the station filter still works. All 48 kitchen tests pass on Postgres.
- **A race test from 24 September was flaky** - `test_kitchen_starts_cooking_while_the_order_is_cancelled` runs its race three times on the same table. When the kitchen wins a round, the order rightly stays open with the dish cooking, and the next round's new order on that table broke the one-open-order-per-table rule. It passed until a CI run where the kitchen won early, which blocked the deploy of the kitchen display fix. Each round now uses a takeaway order, so the rounds can't collide whichever side wins. No app code changed.

---

## 2026-09-25

### Fixed
- **Reports scrolled sideways on a phone** - the tab row shared by all seven report pages (Sales, Kitchen KPIs, Inventory, Menu Engineering, Labor Cost, Discount/Void Audit, CRM Analytics) is about 940px wide, and nothing let it shrink, so on a 390px phone the whole page scrolled sideways. The row now scrolls inside its own strip (one rule in `static/css/themes/luxury.css`, which every signed-in page loads), and `reports/_report_tabs.html` scrolls the current report's tab into the middle of the strip. Desktop is unchanged: all tabs still fit on one line.
- **The inventory report showed a minus sign on money** - the stock ledger stores stock going out as negative, and the report added those rows up as they were. So the Consumption Cost card read "₹-1,234.00", the Consumption and Cost tabs listed negative amounts, and both sorted the *least* used and *cheapest* item first. The Wastage tab also showed "-0.150". `reports/services/inventory_reports.py` now turns movements into positive amounts used, wasted and spent in one helper, sorts the biggest first, and leaves out items that net to zero (a dish cancelled and put back the same day). The Stock Ledger keeps its + and - signs, because there they mean stock in and out.

### Tests
- **7 new tests** in `reports/tests/test_inventory_report_amounts.py`: usage, cost and wastage are positive and biggest first, net-zero items are left out, the page contains no "₹-" and the right totals, the ledger keeps its signs, and the tab strip and its centring script are on the page. Two tests from 24 September now expect positive wastage and a left-out net-zero item.
- **Verified** on a 390px phone-sized browser against the local demo restaurant: no sideways page scroll on the Inventory, Sales and Kitchen KPIs reports, the current tab is on screen, the strip swipes, all tabs fit on desktop, no JavaScript errors.

---

## 2026-09-24 (later): cooked dishes, wastage and cancelling whole orders

### Added
- **"Did the kitchen make it?" when a dish is cancelled after it went to the kitchen** - stock is taken when the ticket goes to the kitchen, and cancelling afterwards used to always put it back, even when the dish had been cooked and thrown away. The shelf count drifted high and nothing showed the loss. The cancel and remove-one sheets now ask: **Made, count as wastage** (stock stays down, the use is re-labelled as wastage) or **Not made, put stock back**. The answer is pre-selected: not made while the ticket is fresh, made once the dish is cooking, ready or served, or once its ticket has been with the kitchen for 10 minutes (`MADE_AFTER`, because many kitchens never tap "start"). Ready and served dishes are locked as made. Anyone can call a dish wasted; putting a dish the kitchen has probably started back into stock needs a manager or owner, so losses can't be hidden. `cancel-item` and `reduce-item` accept `"made": true` or `false` (anything else is refused); leaving it out uses the suggestion. `live-orders/data` and `running-order-data` send `in_kitchen`, `made_locked`, `suggest_made` and `restock_needs_manager` for each line.
- **Stock movements point at the cancelled dish** - new nullable `InventoryTransaction.order_item` (migration `inventory 0017`). Not made: one positive `consume` row per ingredient, a reversal of the original use. Made: a positive `consume` row plus a matching negative `wastage` row, stock unchanged. Either way usage, food cost and the variance report net to zero for a dish that was never sold, and a cooked one shows up as wastage.
- **Wastage report shows where the losses came from** - Inventory report, Wastage tab: filter chips All / From cancelled dishes / Logged by hand, a Cost column and a Wastage Cost card (at cost price), and a "Dishes cancelled after the kitchen made them" table (when, table or token, order, dish, quantity, reason, who, cost). The chosen tab and filter survive the date filter reloading the page.
- **Production refuses to start on SQLite** - `core/db_guard.py`, called from `core/settings.py`. `DB_ENGINE` falls back to SQLite when it is missing from `.env`, so a production server with that line missing would have booted quietly on an empty file outside every backup, and `select_for_update` (every row lock that stops double cancels) does nothing on SQLite. With DEBUG off and a non-Postgres engine the app now stops with a message naming the settings to add. `ALLOW_NON_POSTGRES=1` is the deliberate override. Laptops, the demo recorder and CI (all DEBUG on) are unaffected.

### Changed
- **Cancel Order no longer leaves made dishes behind** - it used to cancel only the dishes not yet served and close the order with the served ones still marked "served" on a cancelled order, where no report counted them. `cancel_whole_order` in `orders/services/void_service.py` now cancels every dish. If some were already made, the first call changes nothing and answers 409 with the list; the screen then offers "Keep them and bill" or "Cancel them as wastage", which needs a reason, plus a manager if any were served. Every screen with a Cancel Order button (Running Order, Billing, Token Billing, the token dashboard and the QSR bill page) now uses the same sheet from `static/js/order_edit.js`.

### Fixed
- **Deadlock when two people act on the same order at the same moment** - cancelling a whole order locked the order and then its dishes, while cancelling, reducing, discounting or comping a dish, and the kitchen's start/ready/served taps, locked the dish and then the order. Two of those at once could each wait for the other; Postgres then kills one and that person sees a server error. Found by the new race tests (cancel order vs cancel dish produced a 500). Every one of those paths now locks the order first (`orders/services/row_locks.py`), and the dish lock no longer locks the menu item row as well.
- **The token dashboard cancelled orders without asking** - its confirm check tested a result object, which is always truthy, so tapping the X on a token cancelled the order straight away. It now uses the shared cancel sheet.
- **The stock ledger showed "--0.500"** for stock going out. Stock coming in now shows "+".
- **Ingredient rows were locked in no guaranteed order** - both returning stock on a cancel and deducting it when a ticket is sent now lock ingredients `order_by("id")`. The deduction path's comment already said "by ID", but sorting the list of ids doesn't make Postgres lock them in that order; only `ORDER BY` does. Without it a cancel and a ticket being sent could deadlock on two shared ingredients.

### Tests
- **45 new tests.** `orders/tests/test_cooked_dish_cancel.py` (44), built on real kitchen tickets (`create_kot`) so the deduction and its ledger row are the real ones: the made/not-made suggestion by status and the 10-minute rule; the hints sent to the board and the Running Order page; stock and ledger rows for every outcome of cancel and remove-one; the manager rule for restocking, with ready and served never restockable; `made` values other than true/false refused; usage and the variance report netting to zero with the loss showing as wastage; the wastage filter's quantities and costs, the cancelled-dish table, and tenant and branch isolation; whole-order cancel asking first and changing nothing, needing a reason, needing a manager for served dishes, leaving no dish behind, freeing the table, refusing another tenant's or branch's order and a waiter; three real concurrent-request races (cancel order vs cancel dish, the kitchen starting a dish while the order is cancelled, two staff cancelling the same order); and the production database guard. `orders/tests/test_cancel_delegation.py`: the served-dish tests now expect wastage instead of stock coming back, and whole-order cancel is covered in both its "asks first" and "manager confirms" forms.
- **Verified** on Postgres with the full suite (1,454 tests, all passing) and with a real-browser run on the local demo restaurant: a cashier on a phone in dark mode sees "Made" pre-selected for a 15-minute-old ticket and "Not made" locked with "Manager needed"; the cancel writes the matching `consume` and `wastage` rows; the owner's Cancel Order lists the made dishes, asks for a reason, then cancels everything, frees the table and returns to the floor plan; the wastage report's filter, cost column and cancelled-dish table show the right rows and keep the tab across reloads; no JavaScript errors.
- **Noticed, not changed here:** the shared report tab bar makes every report page wider than a phone screen (941px on a 390px screen), and the inventory report's Consumption Cost card sums the stored (negative) `consume` rows, so on a normal day it shows a minus sign.

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
