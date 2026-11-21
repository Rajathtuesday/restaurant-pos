# Changelog

All notable changes to Rasova are recorded here, newest first. This file starts
2026-09-05: it is not a retroactive rewrite of the full project history.
For anything earlier than the "Recent history" section below, `git log` is
the source of truth.

---

## 2026-11-21: The header is one row that fits the screen

The floor plan's header was 13 controls and wrapped to three rows on a tablet (768 px). Agreed from a live preview (`md_files/header_icons_preview_2026-11-21.html`).

### Changed
- **One row, always.** `static/js/core/header_fit.js` measures the header and fits every control marked `data-fit` (name, importance, icon) into one row, again whenever the screen changes size: names on the most important buttons as width allows, icons for the rest, and what still does not fit under a `...` More menu. The core floor buttons (Tables, Billing, Combine, Transfer, Calls, Alerts) keep their names from tablet size up; less used ones (Live Orders, Kitchen, Reports, Clock In/Out, Theme, Logout) fold first. A less important button never has its name while a more important one does not. Folded controls stay on the page (their badges and handlers keep working); the More menu repeats them, with their badges and a total on the More button.
- **Icons** (Bootstrap Icons v1.11.3, all in Rasova's own copy): Combine `bi-link-45deg`, Transfer `bi-arrow-left-right`, Live Orders `bi-broadcast`, Billing `bi-receipt`, Kitchen `bi-fire`, Reports `bi-bar-chart-line`; Clock In/Out is now `bi-person-badge` (it was a clock, almost the same as Order History's).
- The floor page's separate phone and desktop copies of its links, and the billing screen's separate phone row of badges, are one control each now.
- **The busy screens' own buttons fit too:** billing (Floor Plan, Kitchen, Reports), the bill page (Print, Thermal Print), the running order (Tables, Cancel Order), live orders (Tables, Tokens, Kitchen) and the token counter (Tokens, Kitchen, Reports, Large Text). Their separate phone copies are gone. Setup, reports, inventory, shifts and the rest keep their own buttons as they were until they are marked.
- In Rasova's theme (luxury buttons, theme colours, light and dark); without the script the header keeps its old wrapping and phone scroll.

### Added (freeze lifted by the owner for this one setting, see CLAUDE.md)
- **Each person's style**, in the More menu: Fit the screen (default), Icons and words, Icons only, Words only. Saved on their account (`User.header_style`, migration `accounts.0012`, additive; `POST /account/header-style/`), so it follows them to any device.

### Tests
- `static/js/tests/header_fit.test.mjs` (7): every style fits every width; wide, everything named; on a tablet the core buttons keep names first; names strictly by importance; on a phone the least used go under More; icons and words never fold a name; icons only and words only hold.
- `core/tests/test_header_fit_browser.py` (6, Chromium): the busy screens (billing, the bill, the running order, live orders, the token counter) are one row with no sideways scroll and no browser error at 390, 768 and 1366 px; and on the real floor page: one row and no sideways scroll at 390, 768, 1024, 1366 and 1920 px; names from tablet size; a phone folds the least used; More lists what folded and saves a style (every button an icon after Icons only); a saved style is used on the next page.
- `accounts/tests/test_header_style.py` (5): Fit the screen by default; a person saves only their own style; an unknown style is refused; no login, no saving; the header carries the person's style. `core/tests/test_one_header.py` follows the billing badge's new markup.

## 2026-11-21: QR Orders badge, the service worker, and 13 CodeQL alerts

### Fixed
- **The QR Orders badge counted closed and cancelled bills.** A guest's dish never approved before its bill was closed or cancelled kept the badge at 1 over an empty floor. It now counts open bills only (`orders/api.py`).
- **On the floor page the QR Orders button linked to the floor page itself.** It is no longer shown there (the floor shows those tables as REVIEW, and the new-order pop-up still comes); it stays on every other screen.
- **The service worker answered some requests with nothing** ("Uncaught (in promise) TypeError: Failed to convert value to 'Response'", 40 times on the live floor page). A file from another host (Cloudflare's analytics beacon, which Brave blocks), or one of our hashed files with no network and no kept copy, got `undefined`. Now another host's files are left to the browser except images, and with no copy and no network the answer is a network error (`Response.error()`).
- **CodeQL js/xss-through-dom (7):** addresses read back out of the page (its data block) were used to navigate or as a link without a check. `static/js/core/site_path.js` (`sitePath`, `goTo`) lets through only a path on this site; the floor, Kitchen Stations, menu recipe import, token counter, dispatch, bill close and receipt use it, and the token screen's buttons escape the order id.
- **CodeQL py/stack-trace-exposure (3):** the Box print queue stored Python's exception text ("ConnectionRefusedError: [WinError 10061]...") as the job's error, which Setup's Test Print shows the owner; now a sentence, the exception in the log. A table merge's refusal went out as `str(e)`; `MergeError` is a `UserError` and the view answers through `error_response`.
- **CodeQL, dismissed with reasons (3):** two test-only regexes reading tags from rendered pages (#123, #127, as #87 and #111 before) and the token screen's panel widths in localStorage (#115, as #75 before).

### Tests
- `orders/tests/test_notification_api.py` (3): an open bill with a dish waiting counts, closed and cancelled do not; the QR Orders button is on billing, not on the floor. Two fail on the old code.
- `static/js/tests/sw.test.mjs` (5): `static/sw.js` run in a sandbox with a fake network and caches: another host's script is left to the browser, an image or a hashed file with no copy offline is a network error, kept copies and the network still serve. Three fail on the old file.
- `static/js/tests/site_path.test.mjs` (3): only site paths pass; the token screen never carries a hostile address or id (fails on the old file).
- `box/tests/test_box.py`: a printer that does not answer fails the job with a sentence, never exception text (fails on the old code).

## 2026-11-21: A bill never asks for less than has been paid on it

Reported from the pub: a cover paid part of a bill; then the guest took fewer items and a promo went on, and the bill came to less than the cover had paid. The screen said "FULLY PAID" with nothing to give back, the cover counted the whole payment as spent, and the bill could not be closed.

### Fixed
- **Nothing stopped a bill dropping below what was paid on it.** Voids, fewer of a dish, dish discounts, bill discounts, promos, comps and the parcel charge checked only whether the bill was settled. Now `Order.recalculate_totals`, where every change to a bill ends, calls `orders/services/paid_guard.py`: when the new total is below what is paid, **covers come down first** (a cover pays only what is due; the difference is back on the cover for its night, no money moves, recorded as `payment_refunded` with `"to": "cover"`), then **money paid (cash, UPI, card) is given back**: by default on the spot, with a manager doing the change or approving it with their PIN (the same PIN prompt as a printed-bill change), recorded as an approved refund against the payment and a negative `REFUND-<id>` payment, so shift close takes cash out of the drawer count. A refund already asked for is not given back twice. Without a manager the change is refused and nothing of it stays (the caller's transaction).
- **"Paid" was counted in about a dozen places, each skipping refund rows**, so a refunded rupee still counted as paid. `Order.amount_paid()` (payments less refunds) and `Order.amount_due()` (nothing once settled) are the one count, used by the bill screen, payments, covers, Razorpay, the token counter, the guest bill page, close without payment and the receipt. Reports keep showing refunds apart.
- **The bill screen hid an overpaid bill as "FULLY PAID".** It now shows "Overpaid ₹X" in red with a Give Back button; refunds show as "Given back"; a bill paid in full exactly shows "Close Bill (Paid In Full)", and paying ₹0 closes it (it used to be refused unless the total was 0). An overpaid bill cannot be closed until the difference is given back.
- **Give Back Money** on the bill (`/give-back/<order_id>/`): a manager, or anyone with a manager's PIN, gives money back on an open bill, up to what was paid in money.
- **`python manage.py bills_below_paid`** lists open bills left overpaid before this fix; `--apply` brings their cover payments down to the bill. Money paid is never moved by it: those bills are listed for a manager to give back on the screen.

### Added (freeze lifted by the owner for this one setting, see CLAUDE.md)
- Setup, Outlet Settings: **When a bill drops below what's paid**: a manager gives the difference back on the spot (default), or the difference is given back first and the change refused until then (`Outlet.bill_below_paid`, migration `tenants.0043`, additive).

### Tests
- `covers/tests/test_bill_drops_below_paid.py` (19): a void, fewer of a dish and a discount after a cover bring the cover down and leave the rest on it; after cash, a cashier alone is asked for a manager, with a PIN the difference is given back, a manager gives it back themselves, a pending refund is not given back twice; refund first refuses, then allows after a give-back; the void screen answers with the PIN prompt and then gives back; an already overpaid bill shows "Overpaid", cannot close, needs a PIN to give back, then shows paid in full and closes; no more than was paid goes back; another restaurant's bill is not found; the repair command lists, then applies. All fail without the guard.
- `orders/tests/test_running_order_browser.py`: a captain makes a dish free on a bill with 100 paid in cash; the real page asks for a manager's PIN saying "gives back ₹100.00", and with it the dish is free and 100 is given back. The comp-sheet test now uses a bill with nothing paid.
- `setup/tests/test_setup.py` (3): the choice saves, an unknown value is ignored, the page offers both. Node: the amount to give back is checked on the screen.

## 2026-11-21: The undo puts back exactly the old libraries

The undo (`bash deploy.sh --undo`) was rehearsed on production for the first time: 55b58ca back to d50fabb, the site up (200), one celery beat, the old code running on the new schema; then `bash deploy.sh` brought 55b58ca back. Recorded in `docs/DEPLOY.md` and `docs/RUNBOOK.md`.

### Fixed
- **The undo left behind the libraries the undone version added.** It reinstalled the saved `pip freeze`, but `pip install` only puts versions back; it removes nothing. On the server the new version's `httpx2` stayed and pip reported a clash with the old `idna`. The undo now also uninstalls every library the saved list does not name.

### Tests
- `core/tests/test_deploy_script.py`: the pip stand-in behaves like pip (install leaves other libraries, uninstall removes them), version B adds a library, and a new case checks the libraries after an undo are exactly A's (fails on the old script: `extra==1` left). The case where B does not come up checks the same.

## 2026-11-21: Browser and PDF tests give the same answer on Linux and Windows

First run of CI's steps in a Linux container (Docker, the way GitHub runs them): 2,629 tests, 9 failures, none on Windows.

### Fixed
- **Five browser tests failed on Linux only** (Kitchen Stations, the smoke walk). The page asks the local print agent (`ws://localhost:8765`) on load; no agent runs beside a test; Linux refuses at once and Chromium logs `ERR_CONNECTION_REFUSED` as a console error, while Windows retries past the page's own 2 s timeout and logs nothing. `core/tests/browser.py` now gives every test page (through `run_page` or a context a test opens itself) a stand-in for that socket that says "not running" at once, without the network.
- **Four PDF tests had never run on Linux** (WeasyPrint needs libraries Windows lacks). One expected the app folder's copy of a static file, but CI collects static files before its tests, so the resolver rightly read the collected copy; the test now sets an empty `STATIC_ROOT`. The other looked for "164.4" in the PDF's bytes (pydyf compresses the page box) and measured `paper_58.css`; it now links a stylesheet of its own and reads the page size with pypdf.

### Found, not changed (your decision)
- `static/css/print/paper_58.css` and `paper_80.css` say `size: 58mm auto` / `80mm auto`, which is not valid CSS: Chrome and WeasyPrint both ignore it (checked: Chrome prints US Letter with it, 58 mm wide with `58mm 297mm`). So these rules have never set the receipt size; printers have used their own paper setting, and the bill PDF is the default page size. A valid size would change how live receipts print (a fixed height feeds that much paper), so it is left for a decision.

### Tests
- `core/tests/test_browser_local_agent.py` (3): a page asking for the agent gets "not running" with nothing logged, through `run_page` and through a context a test opens; other sockets are untouched. Two fail on the old code on Windows too.

## 2026-11-21: Celery can reach Redis on a fresh install

### Fixed
- **kombu was not pinned**, and celery 5.6.3 asks only for `kombu>=5.6.0`. A fresh install on 21 Nov (a clean Linux container running CI's steps) got kombu 5.7.0, which no longer lists `packaging` among its requirements while its Redis transport still imports it: every task sent to Redis (printing, digests, the nightly jobs) failed with `No module named 'packaging'`. A machine that already had `packaging` from another library never saw it. `requirements.txt` now pins `kombu==5.6.2` (what the test suite runs on) and `packaging==26.1`. A clean Linux install of the new file imports the transport.

- The systemd units' names had a long dash after "Rasova POS"; now "Rasova POS: Celery Beat" and so on. The next deploy reinstalls the beat unit (deploy.sh copies it when it differs). On the server, celery-beat was installed and enabled by hand on 21 Nov before that deploy: 1 beat running, starts on boot.

### Tests
- `core/tests/test_dependencies.py` (3): the Redis transport imports (fails in the clean container with the old requirements); kombu and packaging are pinned; the installed kombu is the pinned one.

## 2026-11-21: Module imports are rewritten once, in any file order

### Fixed
- **collectstatic broke `reports/dashboard.js`, in one of two ways depending on the machine.** Django's import-rewriting patterns are case-insensitive, match anywhere and run across lines and semicolons, so the words "the Export menu," in the file's header comment read as the start of `export ... from "..."` and ran on to the next import, which the import pattern had already rewritten; the address was rewritten twice. On CI (Linux file order) collectstatic stopped with `MissingFileError: js/core/app.<hash>.js`, so the 21 Nov push did not deploy. On Windows it finished and wrote `import ... from "../core/app.<hash>.<hash>.js"`, a file that does not exist: the sales report's scripts would not have loaded in production. Reproduced on Windows by shuffling the file order (3 of 6 orders failed). `core/storage.py` now gives `RasovaStaticStorage` its own patterns: the keyword in lower case, a static import or export at the start of a line, never past a semicolon (Django's templates kept). With them, 8 of 8 shuffled orders build and no module imports a double-hashed name. This replaces the 15 Nov advice to reword such comments.
- **The guard that should have caught it checked three folders.** `core/tests/test_static_modules.py` looked only at `js/core`, `js/billing`, `js/pages` and four top-level files; the page modules moved into a folder each in November and none was checked. It now checks every module under `static/js` (vendor and node tests aside), refuses a double-hashed import, and its own import finder no longer reads the field id `"f-from"` as an import.

### Tests
- `core/tests/test_static_modules.py`: every module's imports resolve after collectstatic (fails on the old storage: `js/reports/dashboard.js` imports `app.<hash>.<hash>.js`); `ImportRewritePatternsTest` (2): the storage rewrites each address once (multi-line import, `import * as`, default import, side-effect import, `export {..} from`, `export * from`, dynamic `import()`) and leaves prose alone (fails on the old storage), and Django's own patterns fall into the trap on the same source.

## 2026-11-21: CI lint passes again

### Fixed
- `ruff check .` (CI's first step) failed on three test lines from 14 Nov, so the 21 Nov push stopped before the tests and nothing deployed: two unused imports in `core/tests/test_shared_parts_browser.py` and an f-string with no placeholder in `core/tests/test_large_pages_browser.py`. Removed with `ruff --fix`; lint, `manage.py check` and `collectstatic` pass as CI runs them, and both test modules pass (16).

## 2026-11-19: A browser test's cleanup never runs under a request

### Fixed
- **Browser tests deadlocked in their cleanup once in a few full parallel runs** ("deadlock detected" in flush, then "duplicate key ... tenant name" in the next test of the class, which met the rows left behind). A browser test empties every table when it ends (flush, one TRUNCATE), and a request the page had sent could still be running in the live server's thread; the two locked tables in opposite order. The old guard asked Postgres whether any connection was busy, but a request still in Python (middleware, between two queries) looks idle there, and after 15 s the guard gave up silently. Now `BrowserTestCase` counts the server's requests at the door (`_ServerGate` in `core/tests/browser.py`, wrapping the live server's handler): `run_page` waits for the last one after closing the page, and the cleanup (`_fixture_teardown`) shuts the door, waits, then flushes. A request arriving after that gets a 503 without reaching Django; one still running after 30 s fails the test by its address instead of deadlocking. A request leaves the gate when its body is sent or closed, so a browser that hung up cannot keep it counted.
- Three browser test files (approval PIN, item discount, promo) built their own live server on `StaticLiveServerTestCase` with a copied setup; they now use `BrowserTestCase` and its gate. The seven places that called the old Postgres wait call `self.wait_for_the_server()`.

### Tests
- `core/tests/test_browser_server_gate.py` (7): a request still running when the page closes is waited for (fails on the old code: `run_page` returned while the server was still answering); no test file starts a live server of its own (fails on the old code: three files); the gate waits for a request in flight, turns a late one away with 503 without reaching the app, names an answer that never ends, lets a request go when its body is sent but never closed, and when the app raises.

### Docs
- README, `docs/RASOVA.md`, `docs/TESTING_STRATEGY.md` and `docs/FRONTEND.md` brought up to date: test counts and run times, CI runs the browser tests, the gate and the `BrowserTestCase` rule, KOT numbers across the business day, merge and unmerge rules, money written one way.

## 2026-11-19: Any merged table can be unmerged from the floor

### Fixed
- **A tangled merge could not be undone from any button.** Merges made before the 19 Nov guards could pile up (Table 2 the main table of four merges, then Table 2 merged into Table 1). Every card then showed "Linked", Unmerge showed only on a main table's card, and the server ended one merge per tap, found by its main table alone. Now a joined table's card has Unmerge beside "Go to Primary", and `/unmerge-tables/<table_id>/` takes any table of the merge: `unmerge_around` ends every active merge connected to it (through shared tables), locked in id order, so one tap clears the tangle. A merge not connected to that table, or another restaurant's, is not touched. On unmerge, a table with an open order of its own keeps its state instead of being set free.

### Tests
- `tablemerge/test_unmerge_any_table.py` (6): the demo floor's tangle comes apart in one tap from a joined table and Table 1 keeps its order's state; a joined table ends its merge; a main table of several merges ends them all; an unconnected merge, a table in no merge (404) and another restaurant's merge are left alone. Three fail on the old code (404, one merge of three ended).
- Node: a joined table's card carries Unmerge with its own id (fails on the old floor.js).
- `core/tests/test_shared_parts_browser.py` looks for the period picker by its report bar id (`customDates`); it still asked for the inventory page's old `customRange`.

## 2026-11-19: Money written one way on every screen (V12)

### Fixed
- **Eight scripts still wrote amounts by hand** after the billing screens moved to `rupees()` (core/money.js) on 13 Nov. Order history showed "Rs.21" for Rs 20.50 (paise rounded away by `toFixed(0)`) and refunds as "₹50"; the promo picker said "₹50.00 Flat"; the sales chart tooltip "₹1234.5"; the bill page's change, close-without-payment and promo minimum, the refund maximum, the cart's offer line and the offer setup example each had their own format. All now go through `rupees()`: whole rupees with no paise, two decimals with paise, Indian grouping. The setup example's offer name reads "₹100 off every whisky".

### Tests
- `core/tests/test_money_one_way.py` (2): no script outside core/money.js glues a rupee sign or "Rs." to a number (13 places on the old code); the scan is proved on samples, and a "₹" in a label or placeholder stays allowed.
- Node: order history keeps paise and grouping ("₹20.50", "₹1,25,000", a refund "₹12.50"), and the promo picker writes a server "50.00" as "₹50". Both fail on the old code. Browser expectations follow: the offer setup example ("2,300", not "2300.00"), the QR cart offer line ("−₹50", as its totals), close without payment ("₹400 stays unpaid").

## 2026-11-19: An order open across the business day can still go to the kitchen

### Fixed
- **An order still open when the business day turned over could no longer be sent to the kitchen.** KOT numbers start again from 1 each business day, and an order may not hold two KOTs with one number; a pub order opened before the day's start hour and sent again after it was handed a number it already held, the database refused, and every later Send to Kitchen failed with "Could not send to kitchen". `create_kot` now skips any number the order already holds, under the day counter's lock, so numbers stay unique within the order and still rise through the day. No schema change.

### Tests
- `kitchen/test_kot_day_rollover.py` (2): a send after the day turns over gets a number the order does not hold; the skipped number is used up, so the next send's number is higher. Both fail on the old code.

## 2026-11-19: Two people settling one bill at once: held by tests

### Tests
- `orders/tests/test_checkout_race.py` (2, real threads): two cashiers paying the whole bill at the same moment take it once (one 200, one refused, the order closed); three split shares paid at once never take more than the bill. Checked because a review listed concurrent checkout as open: `pay_order` and `process_payment` lock the order row and `recalculate_totals` locks it again, so these pass on the current code. They stay as guards.

## 2026-11-19: Merging and transferring tables keep the floor and the bills straight

### Fixed
- **A table could be in two merges at once**: merging checked nothing about merges already active. A table already merged (as the main table or a member) is refused: "Alpha Table 2 is already merged. Unmerge it first."
- **A table merged in with its own open order kept it**, off the main table's bill, so the party could be billed twice over or not at all. Such a merge is refused: "Table 3 has its own order. Transfer it or bill it first, then merge." (Moving its dishes onto the main bill is a money change of its own and was not done here.)
- **Moving the order off a merge's main table set that table free and left the merged tables showing occupied with no order.** That transfer is refused until the tables are unmerged.
- **A transfer locked the order but not the tables**, so the "is the new table occupied" check was check-then-act. Both tables are now locked in id order (as merges lock them) before the check.
- Refusals answer 400 with the reason (`tablemerge.services.MergeRefused`), not 404.

### Tests
- `tablemerge/test_merge_transfer_guards.py` (7): a table in a merge cannot join another, nor a merge's main table; a table with its own order is not merged in; free tables still merge; a merge's main order is not moved away; a plain transfer still works (these four refusals fail on the old code); two orders moved to the same free table at once leave one there (real threads; this one did not reproduce on the old code, as the window is narrow, and stays as a guard).

## 2026-11-19: Send to Kitchen tapped twice: one KOT, and no false error

### Fixed
- **A second Send to Kitchen at the same moment answered "Could not send to kitchen. Please try again."** The order row was already locked, so only one KOT was ever made, but the second request found nothing pending and `create_kot` raised a bare `Exception`, which the view turned into a 400. That message told a waiter in a rush to tap again. `create_kot` now raises `NothingToSend` for that case and the view answers success with no KOT ("Nothing new to send to the kitchen."). The Dispatch screen no longer has to recognise the old error text.

### Tests
- `kitchen/test_send_twice.py` (2, real threads): four sends at once make one KOT and all four answer 200; a second send with nothing new says so. Both fail on the old code (one 200, three 400).
- `static/js/tests/billing.test.mjs`: nothing new to send still shows "Order Sent".


## 2026-11-15: The low-stock badge counts low stock

### Fixed
- **The header's "Low stock & system alerts" badge showed 50 over an inventory board with 2 low items.** `/api/notifications/` counted every unread notification, so the kitchen's "Order ready for Table 2" messages (75 in the demo) filled it, capped at 50. It now counts only `low_stock` and `system` notifications (`orders/api.py`).

### Tests
- `orders/tests/test_notification_api.py`: order-ready messages do not reach the badge (fails on the old code).

## 2026-11-15: A page never runs a new module beside an old one

### Fixed
- **After a change, a page's buttons could all stop working until a hard refresh** ("The requested module './page_actions.js' does not provide an export named 'pageSubmitActions'"; a menu item would not delete). Under `runserver`, Django's own static handler sent no `Cache-Control`, so the browser guessed how long each module stayed fresh and ran the new `core/app.js` with an old `core/page_actions.js`; the page's module graph failed to load. `whitenoise.runserver_nostatic` (before `django.contrib.staticfiles` in `core/settings.py`) makes runserver serve `/static/` through WhiteNoise, which sends `max-age=0` under DEBUG. The service worker now fetches a static file without a hash in its name with `cache: 'no-cache'` (it asks the server each time, a 304 when nothing changed), and its cache is `rasova-v7`, so old kept copies go. Production was not affected: its module imports carry hashed names.

### Tests
- `core/tests/test_dev_static_cache.py` (2): runserver comes from WhiteNoise, ahead of staticfiles; the service worker revalidates unhashed static files. Both fail on the old code.

## 2026-11-15: Promos on Restaurant Setup

### Changed
- `/setup/` has a Promos card beside Offers (whole-bill discounts: % or Rs off, a code, a minimum, dates, a usage cap), linking to the existing `/setup/promos/`. Before, Promos was reached only from the Offers page's header. Not a new page: a card on the existing hub.

### Tests
- `setup/tests/test_offer_setup.py`: the setup hub links to Promos (fails without the card).

## 2026-11-15: One name, one dish, per outlet

### Fixed
- **The Add Item form accepted a second dish with the same name** (two "uta", Rs 200 and Rs 170, in one category), though the KOT, the bill and the QR menu show a dish by its name alone. A dish name is now unique per outlet, comparing without case and with runs of spaces as one: Add Item and Edit refuse a taken name (409) and say which dish has it; a dish may keep its own name in another case. The AI menu import and the onboarding steps leave a dish already on the menu as it is (they checked only an exact name in the same category). Names are saved trimmed. One rule: `menu/dish_names.py`.
- Existing duplicates are not touched; rename one of them from the menu screen.

### Tests
- `menu/tests/test_dish_names.py` (7): the same name refused in any case and spacing, and under another category; a new name saved trimmed; renaming to a taken name refused, a dish's own name kept; another outlet may use it; the import keeps the dish already there. Five fail on the old code.

## 2026-11-15: The menu's dish buttons, on one quiet row

### Fixed
- **A dish's Edit, Available, Recipe, AI recipe and Delete buttons were grey native buttons that wrapped onto two rows**: `.btn-action-icon` had never had a style. They are quiet 36 px icon buttons on one row beside the dish's name (a border on hover, red for Delete), and the name takes the room left. "No recipe defined" and the recipe lines use the page's font, not monospace. `static/css/menu/menu_management.css`.

### Tests
- `menu/tests/test_menu_management_browser.py`: a dish's actions sit on one row and are not drawn as native buttons (fails on the old CSS: two rows).

## 2026-11-15: One report bar on every report page; no inline handlers left anywhere

### Fixed
- **Kitchen KPIs looked unlike the other reports**: its tab bar sat boxed inside the page with other dropdowns, and Print and a back arrow were in the header. Every report page (Sales, Kitchen KPIs, Inventory, Menu Engineering, Labor Cost, Discount/Void Audit, CRM Analytics) now includes one bar, `reports/templates/reports/_report_subnav.html`, straight under the header at full width: the tabs, the digest link, branch and period (Custom Range on every page), the page's own actions (Sales: Export, Z-Report; Inventory: the item filter), Print. Its style is `static/css/reports/subnav.css`; the seven copies of those rules left the pages' stylesheets.
- **The template check counted `onclick` only**, so `onchange` and `onsubmit` handlers sat unseen in 9 templates, two of them the browser's `confirm()` (Delete client on the sales dashboard, Change keg), which never shows in the Android app. It now counts every inline `on...=` handler, and none is left: the report filters, Covers' day and CRM's search use `data-action="form.submit"`, the Zomato and Swiggy switches `field.reveal`, and the two confirms a new shared `confirm.submit` (an ordinary form that asks first in Rasova's own sheet, `static/js/core/page_actions.js`).

### Tests
- `reports/tests/test_report_bar_browser.py` (2, Chromium): every report page has the same bar (fails on the old Kitchen KPIs page); Custom Range opens the dates and another period sends the form.
- `core/tests/test_frontend_hygiene.py` counts `handlers` (any inline `on...=`); on the old code it failed on 9 templates.
- `static/js/tests/page_actions.test.mjs`: `field.reveal`, and `confirm.submit` sends only on yes.
- `reports/tests/test_reports.py`: the Kitchen KPIs back-arrow test now checks only that a tableless cafe is never sent to the floor plan (no report page has a back arrow).

## 2026-11-15: Pages use the laptop's width

### Fixed
- **The dashboard sat in a 565 px column on a 1400 px laptop**, and 14 other pages were squeezed the same way (running order, token dashboard, four reports, inventory consumption and bar counts, recipe import review, setup, QR codes, kitchen stations, promos, offers). `<main>` in `base.html` is a flex column, and in a flex column a block centred with `margin: 0 auto` shrinks to its content instead of growing to its `max-width`. One rule in `static/css/rasova.css` (`main > * { width: 100%; }`) lets each page's block take its width; `max-width` and the auto margins still centre it.

### Tests
- `core/tests/test_browser_smoke_walk.py` now also fails on any block inside `<main>` narrower than the room it has (or its max-width): on the old code it listed all 15 pages.

## 2026-11-15: docs/RASOVA.md, the whole system in one file

### Added
- `docs/RASOVA.md`: what Rasova is and where the business stands, the architecture, a request's path, the apps, tenant isolation and the 33 features, money, kitchen, printing, offline and the Box, the screens' rules, the AI, background jobs, deploy, health, backups, security, tests, the working rules, a code map, open items and a glossary. Written for any AI assistant or new developer; built from tracked files only (nothing git-ignored, no keys).
- `README.md` points at it, and no longer says Gemini or lists the removed central kitchen.

## 2026-11-15: The AI runs on Claude (Gemini is gone)

### Changed
- `core/ai_service.py` calls Claude through the Anthropic SDK (`anthropic==2.8.0` replaces `google-genai` in `requirements.txt`). Menus are read by `claude-sonnet-5-5`; recipes, ingredient matching and price suggestions by `claude-haiku-5-5` (`AI_MENU_MODEL` / `AI_RECIPE_MODEL` in `.env` override them). Callers see the same lists and dicts as before.
- Answers come back through structured outputs (a JSON schema per task) instead of stripping backticks off free text. A refusal or an answer cut off counts as a failure, so each task keeps its own fallback: the plain parser for menus, one clear message for recipes, "unmatched" for ingredients.
- The menu call has the API's refusal fallback on (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`): if a safety check declines, another model answers in the same call.
- The client is built only from `ANTHROPIC_API_KEY`. Without it the AI is off, even on a machine with an `ant auth login` profile, so a test or a laptop without the key never reaches the real API.
- The Gemini second-account fallback key (`GOOGLE_API_KEY_FALLBACK`, a free-tier workaround) is gone with Gemini. `.env.example` lists `ANTHROPIC_API_KEY`.
- **On the server:** add `ANTHROPIC_API_KEY=` to `.env`, then restart gunicorn and celery. The deploy installs `anthropic` from `requirements.txt`.

### Tests
- `core/tests/fake_claude.py`: a fake Anthropic client; tests hand it to `AIService(client=...)`. No test calls the real AI.
- `core/tests/test_ai_claude.py` (14): no key means no client even with a login profile; a key builds the client with that key; a PDF goes as a document and a photo as a JPEG image to Sonnet with the menu schema and the fallback; pasted text; a refusal or a cut-off answer falls back to the plain parser; recipes on Haiku and every failure one message; matching drops an invented name; pricing.
- `core/tests/test_ai_service.py`: the fallback-parser tests run on the fake; the Gemini-only tests (second key, `_generate_content` retries) went with that code. `test_no_real_services` also checks `ANTHROPIC_API_KEY` is blank under the test runner.

## 2026-11-15: A cash payment sent twice is recorded once

### Fixed
- **A part payment whose answer was lost was counted twice.** A cash payment taken offline, or whose connection dropped mid-request, is kept on the device and sent again later. If the server had taken the first one, a part payment was simply recorded a second time.
- **A payment that had closed the order came back "could not be synced"** ("Order already completed") although it went through.
- Each payment now carries an id (`request_id`). The counter and the bill page give the online attempt and its queued copy the same id; the server keeps it on the payment (`Payment.request_key`, unique per order) and answers a repeat with the first payment (`"repeat": true`), checked under the order's lock before any refusal. Payments queued before this change have no id and are sent as they were.

### Migrations
- `orders 0073_payment_request_key`: one nullable column on `orders_payment` and a partial unique index. Additive: the previous release keeps running against it.

### Tests
- `orders/tests/test_payment_request_id.py` (6): a replay of a closing payment is answered, not refused; a part payment sent twice is counted once (both fail on the old code); two ids are two payments; no id changes nothing; a bad id is refused; the same id on another order is its own payment.
- `static/js/tests/bill.test.mjs`, `billing.test.mjs`, `offline.test.mjs`: every payment carries an id; the queued copy has the id of the request that dropped; an offline payment gets its own id and sends it; a repeat answer counts as synced.

## 2026-11-15: The window.* bridges are gone

### Changed
- `core/app.js` no longer publishes `window.ui`, `apiClient`, `themeManager`, `offlineQueue`, `offlinePaymentQueue`, `rasovaPlatform`, `updateOfflineBanner`, `refreshNotifications`; `order_edit.js`, `offers.js`, `cart_tax.js` and `approval.js` no longer publish `OrderEdit`, `RasovaOffers`, `RasovaCartTax`, `RasovaApproval`. They were kept for inline page scripts, and the last of those went today. `order_edit.js` takes the shared `ui` through `configure({ui})`; Kitchen Stations imports `platform`.
- `core/storage.py` notes a trap found on the way: a comment saying "export ..." above an import makes collectstatic rewrite that import twice (`MissingFileError`). `test_static_modules` caught it.
- The junk file `hich imports order_edit.js#` (a mistyped command's output) is deleted from the repo root (it was never tracked).

### Tests
- `core/tests/test_static_modules.py`: no script publishes on `window` (and the check sees an assignment).
- `orders/tests/test_offline_order_browser.py` read `window.offlineQueue`; it imports the page's own queue from `core/app.js` now. The comment above `core/app.js` in `base.html` says nothing is published on window.

## 2026-11-15: The page address guard after the last page moved

### Changed
- `core.tests.test_page_addresses` asked for more than 50 hand-written addresses as proof that its scan works. With every page on modules there are none (each address comes from page data built with `reverse()`), so it failed on its own success. A sample now proves the scan instead, and the scan also reads the modules' `api.get/post` calls and skips comment lines, so an address written by hand that comes back is still checked.

## 2026-11-15: Menu management on modules (both pages); the last inline page script is gone

### Fixed
- **Editing a dish on the cafe and franchise menu page cleared its kitchen station and description** (and reset its prep time to 10 minutes): that form has no such fields and the server read "not sent" as "blank". `update_menu_item` now changes a station or description only when the form sends one; the page sends prep time only for a new dish.
- **Delete dish, Delete category and Sync to Branches asked with `confirm()`**, which never shows in the Android app, so none could be done there. They ask in Rasova's own sheet.
- **A delete the server refused (a dish on past bills) reloaded the page with no word**; the reason is a toast now. Create category, the station switch, the Takeaway / Zomato / Swiggy switches and Add Ingredient show a refusal too, where they reloaded or (Add Ingredient) said "integrated" whatever happened.
- **A recipe quantity that is not a number ("lots", "-1") gave a 500**; it is refused in words (`core.validators.read_number`).
- The AI menu and recipe imports reported through `alert()`; they are toasts, and a dropped status answer is asked again instead of leaving the button stuck.

### Changed
- `/menu/` (both templates) runs on `static/js/menu/management.js` and `manage_parts.js`; each dish's edit values and the addresses come from `menu.views.management_views.menu_page_data()`; style in `static/css/menu/menu_management.css` and `qsr_menu_management.css`. Delete dish, Delete category, Toggle and the platform switches are shared post buttons.
- **The ratchet is empty**: no template holds an inline script, a style block or an `onclick` any more.

### Tests
- `menu/tests/test_menu.py`: an edit without station or description keeps them; an empty station sends the dish to the default; a recipe quantity that is not a number is refused in words (the first and third fail on the old code).
- `menu/tests/test_menu_management_browser.py` (8, Chromium): delete dish and category ask in a sheet, a dish on past bills says why it stays, a refused ingredient says why, edit a dish, search, the cafe page keeps a dish's station on edit, the availability switch.
- `static/js/tests/menu_management.test.mjs` (8): the form's fields per page, checks, search, the imports' poll answers, polling through a dropped answer, posting a form, the sync's words.

## 2026-11-15: Rasova's confirm sheet shows its title as text

### Fixed
- **`ui.confirm` gave its title to SweetAlert as HTML.** Titles carry names people typed (Remove "Bar"?, a dish's name on Delete), so a name written as markup would have run in the owner's browser. The title is text now (`titleText`), as `ui.ask` already was.

### Tests
- `static/js/tests/small_pages.test.mjs`: the confirm title is text (fails on the old code).

## 2026-11-15: Purchase orders on modules; a second draft for a supplier is refused in words

### Fixed
- **A New PO for a supplier who already had a draft gave a 500.** The database allows one draft per supplier (`unique_draft_po_per_supplier`) and the view never asked first. It now refuses with the draft's number ("Edit that one, or mark it ordered first"), also when two taps race.
- **Order and Cancel asked with the browser's `confirm()`**, which never shows in the Android app, so a PO could not be sent or cancelled there. Both are shared post buttons asked in Rasova's own sheet.
- The Receive drawer wrote ingredient names into the page as HTML (the New PO drawer's list too); they are text now.

### Changed
- `/inventory/purchase-orders/` runs on `static/js/inventory/purchase_orders.js` and `po_parts.js`; data and addresses from `inventory.views.purchase_orders_page_data()`; style in `static/css/inventory/purchase_orders.css`. Requests go through the shared client (refusals and dropped connections are toasts) instead of `fetch()` by hand; a second tap on Save or Confirm Receipt is ignored.
- The ratchet holds 2 templates (was 3).

### Tests
- `inventory/tests/test_po_one_draft.py` (2): the second draft is refused in words (500 on the old code); once ordered, a new draft is made.
- `inventory/tests/test_purchase_orders_browser.py` (5, Chromium): Order and Cancel ask in a sheet, an ingredient name shows as text in Receive (these three fail on the old page), receiving part of an order, a new PO from the drawer.
- `static/js/tests/purchase_orders.test.mjs` (4): the total, line checks, what is still owed, the receive body.

## 2026-11-14: Kitchen Stations on modules; One Printer mode and Remove work in the Android app

### Fixed
- **Switching to One Printer mode and removing a station asked with the browser's `confirm()`**, which never shows in the Android app, so neither could be done there. Both ask in Rasova's own sheet.
- The printer scan wrote the addresses the agent found into the page as HTML, each with an inline handler; they are buttons with text now. The agent's error words are text too.
- The test page printed an arrow ("Agent → Printer") that CP437 printers cannot print; it says "Agent to printer".

### Changed
- `/setup/kitchen-stations/` runs on `static/js/setup/stations.js` and `station_agent.js` (the agent's WebSocket); data and addresses from `setup.views.core_views.stations_page_data()`; style in `static/css/setup/setup_kitchen_stations.css`. Set Default no longer needs a click stopper to keep the card shut.
- The ratchet holds 3 templates (was 4).

### Tests
- `setup/tests/test_kitchen_stations_browser.py` (4, Chromium): One Printer mode and Remove ask in a sheet (both fail on the old page); a station opens and saves its printer; Set Default does not open the card.
- `static/js/tests/setup_stations.test.mjs` (6): the agent's address, Test Print waits for its own job, a refusal in the agent's words, no agent, the scan, a test page the printer can print.

## 2026-11-14: Setup, Offers on modules

### Changed
- `/setup/offers/` runs on `static/js/setup/offers.js` with the example bill in `static/js/setup/offer_example.js` (pure, the cart's own offer engine and tax sums); data and addresses from `setup.views.offer_views.offers_page_data()`; style in `static/css/setup/setup_offers.css`. Pause, Remove and Pause all are shared post buttons, Remove and Pause all asked in Rasova's own sheet. Save ignores a second tap while the first is out.
- The ratchet holds 4 templates (was 5).

### Tests
- `setup/tests/test_offer_setup_browser.py` (6, Chromium): the example bill for buy 2 get 1 and for 25% off, a dish name typed as HTML shows as text, Edit loads an offer and saves it, Remove and Pause all ask in a sheet. They replace the Node harness that ran the old inline script.
- `static/js/tests/setup_offers.test.mjs` (5): the example bill's sums, kept quantities, category picks, the default name.

## 2026-11-14: Order history on modules; the detail panel escaped and redrawn after a refund

### Fixed
- **The order detail panel wrote what people typed into the page as HTML**: the guest's name and phone, dish names, void and refund reasons, payment references, audit lines. All of it is text now (`order_history/detail.js`).
- **After a refund request, approval or rejection, the panel never showed the change.** It read its heading back as the order id, but the heading is the bill number (`INV-1-...`), so it asked for nothing. The panel now keeps the order's id.
- **Approve used `confirm()` and Reject used `prompt()`**, which never show in the Android app, so an owner there could not decide a refund. Both are Rasova's own sheets; the refund request is a form (the browser checks the amount and the note first) and refusals are toasts, not `alert()`.

### Changed
- `/history/` runs on `static/js/order_history/` with its addresses from `orders.views.history_views.order_history_page_data()`; style in `static/css/orders/order_history.css`. A row opens with a tap or Enter; Escape closes the refund form, then the panel; a quick second row tap never shows the first order.
- The ratchet holds 5 templates (was 6).

### Tests
- `orders/tests/test_order_history_browser.py` (4, Chromium): a name typed as HTML shows as text; a refund request shows in the panel; Reject asks for its reason in a sheet; Approve asks in a sheet. All four fail on the old page.
- `static/js/tests/order_history.test.mjs` (5): escaping of every typed field, what is left to refund, who sees Refund, Approve and Reject.

## 2026-11-14: The live orders board on modules

### Changed
- `/live-orders/` runs on `static/js/live_orders/` (`board.js` draws the tickets, `loader.js` asks for them, `index.js` wires filters, search and the remove/cancel sheets from `order_edit.js`). Its addresses and poll interval come from `orders.views.live_orders.live_orders_page_data()`; its style is `static/css/orders/live_orders.css`.
- The board redraws only when its markup changed, waits while a sheet is open, a change is saving or the tab is hidden, and drops a late answer to an older question, so nothing moves under the person's finger.
- The ratchet holds 6 templates (was 7).

### Tests
- `orders/tests/test_live_orders_browser.py` (2 more, Chromium): a failed refresh says so and keeps the tickets drawn; the counter filter and the search say "Nothing matches" when nothing does.
- `static/js/tests/live_orders.test.mjs` (7): attention rules, filters and search, escaping, empty and no-match boards, the loader's failures and late answers.
- `test_live_orders_board` checks the page loads `js/live_orders/index.js`.

## 2026-11-14: Setup Staff and the inventory board on modules; purchase order names escaped

### Fixed
- **Generate Purchase Orders put supplier and ingredient names into the page as HTML** (the line that says what it did); every name is escaped now (`inventory/board_parts.js`).
- **The inventory board said everything with `alert()`**, which never shows in the Android app: adding stock, a new ingredient, an edit, wastage and a stock count are shared post forms, so a refusal is a toast and the browser's own checks (a quantity above 0, a reason) run first.

### Changed
- Off the ratchet: Setup Staff (`setup/staff.js`, `staff_sheets.js`: the role, location, pay rate and password sheets have text titles; Deactivate is a post button; "Show inactive" is CSS `:has`), the inventory board (`inventory/board.js`: the Edit, Waste and Adjust sheets are filled for the ingredient tapped and point their form at it; Add Ingredient opens by Bootstrap's own data attributes).
- The ratchet holds 7 templates (was 9).

### Tests
- `core.tests.test_large_pages_browser.StaffAndInventoryBrowserTest` (3, Chromium): a role changed from its sheet and a deactivated member hidden until "Show inactive"; a supplier called `<b id="injected">Fresh Farm</b>` shows as text after Generate Purchase Orders (fails on the old page); stock added and wastage logged by their forms.
- `static/js/tests/large_pages_2.test.mjs` (6).

## 2026-11-14: Five large pages on modules; promo delete, ingredient names and the recipe sheet fixed

### Fixed
- **Setup, Promos deleted a promo even on Cancel** (the `ui.confirm` mistake once more). Delete and Pause are shared post buttons now.
- **The promo tiles (active, all outlets, uses) showed "-" until a script counted the rows**; the view counts them and the page renders the numbers.
- **The modifier page's Inventory Link sheet put ingredient names into the page as HTML**; names are escaped (`menu/modifier_links.js`).
- **The modifier page reloaded as if every change worked**: creating a group, adding a modifier, linking a dish ignored the server's answer. They are shared post forms and buttons, so a refusal is shown.
- **The recipe import review asked for a new ingredient with two `prompt()`s, and said everything else with `alert()` and `confirm()`**, none of which show in the Android app. A new ingredient is one sheet (a name and a unit list, `inventory/recipe_lines.js`); the rest are toasts and Rasova's own sheets.
- **The setup wizard's AI import put the server's error message into the page as HTML**; it is text now.

### Changed
- Off the ratchet: the setup wizard (`setup/onboard.js`; the menu cards answer Enter and Space, their hover border is CSS), the recipe import review (`inventory/recipe_review.js`), the sales report (`reports/dashboard.js`, `dashboard_parts.js`; the Export links are real links, the filters added on tap), Setup Promos (`setup/promos.js`, `promo_form.js`), modifier groups (`menu/modifiers.js`; Create Group opens its sheet by Bootstrap's own data attributes).
- Shared parts: `data-go` on a post button (go to an address once done: Discard an import goes back to the menu); `field.upper` is a shared input action (promo codes, GSTIN, bill codes).
- The ratchet holds 9 templates (was 14).

### Tests
- `core/tests/test_large_pages_browser.py` (5, Chromium): a promo delete cancelled keeps it and the tiles are numbers; an ingredient called `<b id="injected">Salt</b>` shows as text; a new ingredient added from the recipe review in one sheet (all three fail on the old pages); the sales report's two charts draw; the wizard checks a web address as it is typed.
- `static/js/tests/large_pages_1.test.mjs` (12); `post_actions.test.mjs` gains `data-go`.

## 2026-11-14: Six mid-size pages on modules; a shift kept on Cancel, O'Brien can be scheduled

### Fixed
- **The schedule builder deleted a shift even on Cancel** (the same `ui.confirm` mistake as shift templates). Removing a shift is a shared post button now.
- **Nobody called O'Brien could be given a shift.** The schedule builder put the staff member's name straight into an `onclick`; an apostrophe broke the button ("missing ) after argument list"). The empty cells are buttons carrying the name as data.
- **The pickup display board drew a token's text unescaped**; it is escaped now.
- **The token dashboard's 15-second refresh could reload the page while the Cancel sheet was open**; it waits while a sheet is open or the tab is hidden. Cancelling the last token now shows the empty state (it only rendered when the page loaded with none).

### Changed
- Off the ratchet: the schedule builder (`shifts/schedule_builder.js`; assigning is a shared post form, the server's "a template, or both times" check is what the person sees), suppliers (shared form and button; the search reuses the inventory row filter; its own toast box is gone), the pickup display board (`tokens/display_board.js`), outlet settings (`setup/outlet_settings.js`; the ticked option boxes light up by CSS `:has`), GST rates (`menu/gst.js`, `gst_rows.js`; each category's title is a button that folds it), the token dashboard (`tokens/token_dashboard.js`, `refresher.js`; Mark Ready, Picked Up and the Zomato and Swiggy switches are post buttons).
- The ratchet holds 14 templates (was 20).

### Tests
- `core/tests/test_mid_pages_browser.py` (6, Chromium): a shift delete cancelled keeps it and O'Brien is assigned a shift (both fail on the old page), the board shows a ready token, a dish's GST rate saves, a ticked option box lights up and a short GSTIN is flagged, a supplier is added.
- `static/js/tests/mid_pages.test.mjs` (9).

## 2026-11-14: Post buttons and forms written once (V1); twelve more pages on them

Most of what a page does is "send this, then reload or take the row away". Every page wrote that itself, a little differently, and some got it wrong.

### Fixed
- **Deleting a shift template asked "are you sure?" and deleted even on Cancel.** `ui.confirm()` answers with an object, which is always true; the page tested the object.
- **Pending refunds could not be approved or rejected in the Android app**: Approve used `confirm()`, Reject `prompt()` for its reason. Both are Rasova's own sheets now, the reason optional as before.
- **A refused reservation change said so with `alert()`**, which the app never shows; the shared client's toast says it now.
- **The control panel's preset buttons** on its list page used `confirm()` and `alert()`: Rasova's sheets now, the same as the restaurant page (`static/js/accounts/preset.js`).

### Changed
- **`static/js/core/post_actions.js`** (V1, the first shared part): a button with `data-action="post"` and `data-url` sends, then reloads (`data-then`) or takes a row away (`data-remove`); `data-confirm` asks first, `data-ask` asks for a line of text (`data-ask-optional`), `data-done` toasts. A `<form data-action="post">` sends its named fields as JSON when submitted, so the browser's own checks (`required`, `min`) run first. A 200 carrying `error` is a refusal; a second tap while the first is out is ignored. `core/app.js` handles it on every page, so these pages need no script of their own.
- **Change actions** in `core/page_actions.js`: `form.submit` (a filter sends its form) and `range.pick` (a report's period: Custom shows the dates, anything else sends).
- **`ui.ask(..., { required: false })`**: an optional answer, `""` when left empty.
- Off the ratchet, on the shared parts with no script of their own: expenses, report subscriptions, shift templates, pending refunds, reservations (the date pickers are small GET forms). With a small module each: the shifts page (`shifts/clock.js`, the live clock), the waiter dashboard (`waiter/live.js`: reloads when the list changes, waits while the tab is hidden), the consumption and variance reports (one shared `inventory/row_filter.js`), the inventory report (`reports/inventory_report.js`: its tabs are a real tab list), the feature switches (`accounts/feature_toggle.js`), the control panel (`accounts/create_restaurant.js`). Their `oninput=`, `onchange=`, `onsubmit=` and `onmouseover=` went too, and their form fields gained labels tied to them.
- The ratchet holds 20 templates (was 32).

### Tests
- `core/tests/test_shared_parts_browser.py` (8, Chromium): a template delete cancelled keeps it, confirmed removes it (fails on the old page: the row went on Cancel); a refund rejected with no reason in the sheet (fails on the old page: no sheet); a reservation confirmed; an expense recorded by its form and deleted; a waiter call resolved; the inventory report's tabs and custom period; a feature switch saving an override; New Restaurant opening its page.
- `static/js/tests/post_actions.test.mjs` (20): post buttons and forms (asked, declined, refused, a 200 with an error, a double tap, an optional reason, the fields sent), the change actions, the row filter, the report tabs, the feature switch, the waiter list, the clock, New Restaurant.
- `core.tests.test_page_actions`: no page module handles `post`, `form.submit` or `range.pick` either.

## 2026-11-14: The CSRF cookie fallback reads Rasova's own cookie

### Fixed
- **`static/js/core/csrf.js` fell back to a cookie named `csrftoken`; Rasova's is `csrftoken2`** (`settings.CSRF_COOKIE_NAME`). Every page that posts today has the meta tag, so nothing failed yet, but the first page relying on the fallback would have sent an empty token and been refused. The name is `CSRF_COOKIE` in `csrf.js` now.

### Tests
- `core/tests/test_csrf_cookie_name.py`: `CSRF_COOKIE` equals `settings.CSRF_COOKIE_NAME` (fails on the old code); the node test reads the real cookie.

## 2026-11-14: Eight small pages on modules; tables renamed and presets applied in Rasova's own sheets

### Fixed
- **Renaming a table works in the Android app.** Setup, Tables asked for the new name with the browser's `prompt()`, which never shows there. It is Rasova's own sheet now: `ui.ask()` in `static/js/core/toast.js`, the text-input replacement for `prompt()` (the title is text, never read as HTML). The tables are buttons, so a keyboard reaches them.
- **The control panel's preset buttons work for any restaurant name.** The page put the name straight into its script, so a name with a backtick broke the script ("missing ) after argument list") and no preset applied. The name is data on the button now, and the question is Rasova's own sheet, not `confirm()`.
- **A double tap opens the cash register once.** Start Session and Finalize ignore a second tap while the first is out.

### Changed
- Off the ratchet, each with its script in a module and its CSS in a file: Setup Tables (`static/js/setup/tables.js`, `rename_table.js`), the cash register (`static/js/shifts/`), the browser receipt (`static/js/orders/thermal_receipt.js`), the control panel's restaurant page (`static/js/accounts/`), the login page (`static/js/accounts/login.js`, and `static/js/core/theme_boot.js` in its head for dark mode before the first paint), Printer Setup (the paper width cards and the Advanced switch are buttons; `static/js/setup/mac.js` formats the MAC address), the reports' tab row (`static/js/reports/tabs.js`).
- **The order locked page is on `base.html`**: the one header and its theme switch, not a copy of the theme code with its own colours.
- **The browser receipt's sizes follow the roll in CSS**: `html.paper-58` or `html.paper-80` sets them (`static/css/orders/thermal_receipt.css`), `receipt_page_58/80.css` its `@page`. The view no longer computes the four font sizes.
- The ratchet holds 32 templates (was 40).

### Tests
- `core/tests/test_small_pages_browser.py` (6, Chromium): a table renamed in the sheet; a preset applied for a restaurant called "Bob`s ${bar}"; the printer page's width, Advanced and MAC; the login theme switch lasting past a reload; the register opened once from a double tap; the reports' tab row centred on a phone. The rename and preset tests fail on the old pages.
- `static/js/tests/small_pages.test.mjs` (15): `ui.ask`, renaming, the register, presets, the MAC format, the tab row, the receipt's print on load.
- `reports.tests.test_inventory_report_amounts`: reads that the page loads the tab module, not its old inline script.

## 2026-11-14: A platform superuser's pages stop polling a 500

### Fixed
- **`/api/notifications/` answered 500 for a superuser with no outlet** (found by a browser test on the control panel). `printing_state` (the printer chip's facts, added 13 Nov) read `outlet.pk` of `None`, so every page a platform superuser opened failed its 8-second poll. It answers `None` with no outlet, which the chip already reads as "nothing to say".

### Tests
- `printing.test_printing_status`: a superuser with no outlet gets a 200 and no printing state (fails on the old code).

## 2026-11-14: 29 pages lose their inline stylesheets; the invoice and PO PDFs read their CSS from disk

The 29 templates with a `<style>` block and no script of their own hold markup only now.

### Fixed
- **The subscription invoice and the purchase order PDFs go through `core.pdf.render_pdf`.** `core/pdf.py` said it rendered them, but `billing/services.py` and `inventory/services.py` called WeasyPrint directly, without the fetcher that reads Rasova's static files from disk and refuses anything else. Their stylesheets are static files now, and with the old call WeasyPrint would have fetched them from the server's own public address.

### Changed
- **Each moved stylesheet is `static/css/<app>/<template>.css`**, linked where the `<style>` block was, so the cascade is the same (sales dashboard, agency, invoice, box health and setup, covers, CRM, bar count, kegs, the PO printout, the public bill, feedback and expired pages, six reports, the inspection page, seven setup pages, the demo switch).
- **Shared page buttons** (`static/js/core/page_actions.js`): `page.print`, `page.reload`, `page.back`, `field.select`, handled by `core/app.js` on every `base.html` page and by `core/standalone.js` on a page without it. The 7 `onclick=` on these pages became `data-action`. The bill page and the QR codes page stop handling Print themselves.
- **Removed two dead templates**: `orders/order_timeline.html` (no view renders it) and `core/templates/core/demo_switch.html` (an identical copy of `templates/core/demo_switch.html`, which is the one Django loads).
- The ratchet holds 40 templates (was 71).

### Tests
- `billing.tests` and `inventory.tests.test_inventory`: the PDF is rendered with the own-files fetcher (fails on the old services).
- `core/tests/test_page_actions.py`: no page module handles the shared names; in Chromium one tap prints once on the kitchen report, the bill page and the PO printout.
- `static/js/tests/page_actions.test.mjs` (3).

## 2026-11-14: The running order page on modules (phase 2)

The running order page (`/order/<id>/`) asked why a dish was free with the browser's `prompt()`, which never shows in the Android app, so Comp did nothing there. It also kept its own list of who may do what, and the list had drifted from the server's.

### Fixed
- **Comp opens Rasova's own sheet** (`OrderEdit.complimentary` in `static/js/order_edit.js`, the same sheet as cancel and remove one): quick reasons (birthday, food complaint, long wait, regular guest) or a typed one, at least 3 characters as the server asks. A manager's PIN, when the bill is printed, is asked as for any other change.
- **Captains get Comp.** The server lets a captain make a dish free; the page showed the button to owners and managers only. A waiter no longer sees Cancel Order, which the server refuses them.
- **A dish already free says Free and has no Comp button.** The poll (`running-order-data`) now sends `is_complimentary`; before, Comp stayed on the dish and could be tapped again.
- **A failed refresh says so** above the last list ("Could not refresh this order. Check the connection."); before, it went to the console and the page looked current.
- **Totals** are written the one way (`rupees()`, "₹525", not "₹525.00").

### Changed
- `orders/running_order.html` is on `base.html` (the one header, its theme switch) and holds markup only: `static/js/running_order/` (`view.js`, `loader.js`, `actions.js`, `index.js`) and `static/css/running_order.css`. Its data comes from `running_order_page_data()` as one `page-data` block. The list is redrawn only when it changed, only the newest answer is drawn, a second tap while the first is out is ignored, and the 3-second poll waits while a sheet is open or the tab is hidden. Off the ratchet (71 templates left).
- **`core.decorators.may(user, view)`**: `role_required` and `feature_required` now record their roles and features on the view, and `may()` answers what those checks would. Page data asks it instead of copying role lists (`docs/FRONTEND.md`, "How a page gets its code").

### Tests
- `static/js/tests/running_order.test.mjs` (22): badges, order of dishes, who gets which button, Free, escaping, the empty state; refused, offline, a login page, a late answer; serve, comp, a double tap, cancelling the order.
- `orders/tests/test_running_order_page.py`: `may()` (roles, features, superuser, two role lists), the page data for owner, captain and waiter, `is_complimentary` in the poll, no browser pop-ups in the page or its modules.
- `orders/tests/test_running_order_browser.py`: a captain makes a dish free through the sheet (total to ₹0, Comp gone); a waiter gets no edit buttons; a failed refresh shows its line and keeps the list. The first and last fail on the old page.

## 2026-11-14: The demo pub opens on a Friday night

A stranger opening the demo pub found an empty bar: drinks, a keg and a happy hour set up, but no night to look at.

### Changed
- **`python manage.py seed_pub_demo` plays a Friday night up to now** (`devtools/pub_night.py`): four tables drinking, eight bills paid tonight by cash, UPI and card with liquor taxed at VAT, the register open with a float, and the lager keg tapped at the start of the night and nearly dry, so the keg board shows it and the low-stock alert has gone off. The night is built the way service builds it: the dishes are sent and their recipes leave the stock, and each line takes the tax the till would copy. Each run starts the night over; `--no-night` sets up the pub and leaves its orders alone.

### Tests
- `devtools/tests/test_pub_night.py` (6 tests): the open tables, the paid bills and the register; liquor VAT on the bills; the keg at 80% or more sold, low stock and its alert; tonight's report equals the payments; a second run starts the night over; the cashier can open the floor plan and a bill.

## 2026-11-14: Touch, not hover

A touch screen keeps the last tapped element "hovered": on a tablet, a card that lifted on hover stayed lifted after a tap.

### Changed
- **Every hover lift is for a mouse only.** The 15 `:hover` rules that moved something (table, menu, token, setup, QR code and other cards, the header's icon buttons) sit inside `@media (hover: hover)`, rewritten in place (`core/css_hover.py`). A finger gets a pressed state instead: a tapped button or link dims while pressed (`rasova.css`, `@media (hover: none)`).
- **Controls on the service screens are at least 44px on a phone**: the brand link, the printer chip, the floor plan's card buttons, the bill page's back link, its per-dish discount button (without making the line taller) and promo list, and the token counter's register and live-order links.
- **The token counter's cancel button shows on a touch screen.** It was invisible until hovered, which a finger cannot do.
- The phone header's height budget is 175px (was 160): the floor plan's header is 17px taller for 44px controls, still one row.

### Tests
- `core/tests/test_touch.py`: no `:hover` rule that moves something outside `@media (hover: hover)` in any stylesheet or template (15 before); the finder and the in-place wrap; the pressed state; on a phone, every visible control on the five service screens at least 44px (13 before).

## 2026-11-14: Text a cashier can read at arm's length

The luxury style leaned on very small capitals: labels at 0.6rem (about 10px), some at 0.55rem. Elegant on a laptop, hard on a tablet a metre away in a dim room.

### Changed
- **No text below 12px on the screens used during service**: billing, the token counter and its bill, the floor plan, the kitchen display and the bill page. 67 sizes under 0.75rem in their stylesheets (`rasova.css`, `bill.css`, `billing.css`, `billing_parts.css`, `tables.css`, `kitchen.css`, `token_billing.css`, the token counter's own style block) are 0.75rem now; printed receipts (`@media print`) keep their sizes.

### Fixed
- **Fields on a touch screen are 16px** (`rasova.css`). The V13 rule meant to stop an iPhone zooming the page on a tapped field applied to iPhones only, and lost to any page's own field class, so the bill page's discount, guest and points fields (13 to 14px) still zoomed it.
- **The bill page shows when each payment was taken.** It read a field Payment does not have (`created_at`, not `paid_at`), so every payment said "Cash ()".

### Tests
- `core/tests/test_text_floor.py` (Playwright): the five service screens on a laptop and on a phone, no visible text under 12px and, on the phone, no field under 16px. It found 81 problems before this change.
- `orders/tests/test_bill_payment_time.py`: a payment's time on the bill page (failed on the old template).

## 2026-11-14: The colour walk: every page, light and dark

The black page behind white cards (12 Nov) sat in the code for days because no test looked at a colour. The theme test checks one page at the root; this walks them all.

### Fixed
- **Bootstrap's own colours follow the theme.** Dark mode is `html.dark`, not Bootstrap's `data-bs-theme`, so in dark mode `.text-muted`, tables and anything inheriting Bootstrap's body colour drew near-black text on the black page: "No recipe defined" on the menu, the inventory board's units and values, "End of Report", the whole promotions and offers setup pages. `static/css/themes/luxury.css` now sets Bootstrap's colour variables from the theme's tokens in both modes, and in dark gives Bootstrap's cards, modals and fields the panel colour so light text stays readable on them. The guest QR menu's stylesheet does the same for its `.text-muted`.
- **The QR menu's Place Order button in dark mode** was white words on a near-white button. Its words are the page's background colour now.
- **The purchase order printout's timeline** drew a finished step's tick white on white (`background: currentColor` on a white-text dot).

### Tests
- `core/tests/test_colour_walk.py` (Playwright): every page the owner can open (the smoke walk's list), once in light and once in dark. An app page's colour must be the mode's, and no visible text may be below a 1.5 contrast against the solid colour behind it. On the code before these fixes it found 25 problems on 11 pages; now none.

## 2026-11-14: No long dashes left in any template

### Changed
- **The 176 em dashes in 45 templates are gone** (V14), some in text people read: page titles ("Setup · Restaurant"), onboarding buttons ("Continue: Add Menu"), role choices ("Cashier: takes payments"), report headings ("Staff cost vs. revenue · 1 Nov to 7 Nov"), select prompts ("Select supplier"), a week's dates ("Nov 9 to Nov 15"). A value that is missing shows "-". Each line was read and given a colon, a full stop, a comma, brackets or a middle dot, whichever the sentence needed.

### Tests
- `core/tests/test_frontend_hygiene.py` fails on any template with an em dash, the marketing site included (it failed on the 45 before this change). `core/tests/test_public_pages.py` wrote the character it checks for literally; it is escaped now.

## 2026-11-14: A runbook for the night it breaks

### Added
- **`docs/RUNBOOK.md`**: "It is Saturday, 9 pm, and..." the site is down, printing stopped, payments fail, the disk is full, everything is slow. For each: how to tell, the two things to try, and when to undo the deploy or restore. Two rules up front: a deploy in the last hour is undone first (`bash deploy.sh --undo`), and the database is never restored to fix a slow or broken site. `docs/DEPLOY.md`'s troubleshooting part points to it.

### Tests
- `core/tests/test_runbook.py` (6 tests): every script and doc it names exists; every log it names is one the settings or a systemd unit writes; every service it restarts is ours or the system's; `--undo`, `--keep` and `versions/last/before.sql.gz` still exist; the `/health/` checks it quotes are real checks; the 15 second timeout and the slow query line match the code; no long dashes.

## 2026-11-14: The parcel charge on the screen comes from the bill's own rule

In October the cart showed 252 and the bill 264: the screen had its own idea of the parcel rule. The screen's copy was fixed, but nothing held it to the server's, as the tax and offer engines' copies are held.

### Changed
- **The rule is one function, `orders/services/parcel.py`**, which `toggle_parcel` now calls instead of working it out inline. `static/js/billing/parcel.js` is the screen's copy and says so.
- **The parcel button says the rate the one way money is written**: "Add Parcel ₹2.50/item", not "₹3/item" (the templates rounded it with `floatformat:0`). The words come from the billing page's data (`parcelOffer`) on both billing screens. The hidden parcel row no longer prints the outlet's rate as its amount: the screen draws the charge itself before showing the row.

### Tests
- `orders/tests/test_parcel_parity.py` (4 tests, needs Node like the other parity tests): 300 random carts through the screen's own cart and parcel modules against the server's rule, to the paisa; a real billing page's data (the outlet's rate, each dish's `data-parcel`) through the same modules against `toggle_parcel` on an order of the same dishes, flat, per item, a dish's own charge, and paise; the button's words. A copy of the rule that adds the outlet's rate to the dishes' own charges fails 241 of them.

## 2026-11-14: Limits on the database

On a 1 GB server one slow query can hold everything: a gunicorn thread, its locks, its memory, and every request queued behind it. Nothing stopped a web query from running for minutes, nothing said a page was getting slower, and nothing checked that the processes could not open more connections than Postgres allows.

### Added
- **A 15 second statement timeout for web requests** (`core/db_limits.py`). `core/wsgi.py` and `core/asgi.py` ask for it before Django loads its settings, and `core/settings.py` turns it into the connection's `statement_timeout`. Migrations, Celery and the nightly backup load the settings without `wsgi.py` and keep no limit, on purpose.
- **A slow query log**: `SlowQueryMiddleware` writes any web query over 500 ms to `logs/errors.log` (`pos.slow_sql`) with the address that ran it.
- **`/health/` reports database connections** open against what Postgres allows, and fails at 80%.
- `docs/DEPLOY.md`, "Database limits".

### Tests
- `core/tests/test_db_limits.py` (17 tests): a web process gets the timeout and a management or Celery process does not (each in a fresh Python process); the option really cancels a long statement on Postgres; slow, quick, long and cancelled statements in the log; the middleware runs a real request's queries through it; the systemd units' connection budget (gunicorn twice for a reload, Celery, beat, migrate, backup, two people) stays below 80% of what Postgres allows, and a unit asking for too many is caught; the health check, open and too many.

## 2026-11-13: The frontend rebuild, phase 2: the guest QR menu

The only Rasova screen a guest sees, and the largest page left: 803 lines of inline script, 518 of inline style and 21 `onclick` attributes, as a standalone page with its own head. It is markup only now, on `templates/core/public_base.html`.

### Changed
- **Its code is `static/js/qr_menu/`**: `cart.js`, `modifiers.js` (a dish's options), `order.js` (placing the order, a second round on the same bill), `status.js` (the status pill and sheet), `view.js` (what it draws, every name escaped), `index.js`, and `boot.js`, which sets dark mode before the first paint. Its style is `static/css/qr_menu.css`. Its addresses, the outlet's tax rules and the CSRF token reach it in one block (`menu.views.customer_views.qr_menu_page_data`); its structured data for search engines is built on the server and escaped.
- **No browser pop-ups for guests.** A missing required option is said inside the options sheet; clearing the cart takes two taps instead of `confirm()`; every other message uses the page's own toast. `alert()` and `confirm()` showed the server's address and looked broken on a phone.
- **A guest's order carries a request id**, like the billing screen's: a tap again after a lost connection cannot order twice.
- After an order is sent the cart empties and the status pill starts at once; the page no longer reloads itself.
- **Veg and non-veg come from the dish's own setting** (`is_veg`). They were guessed from the dish's name ("chicken", "mutton", "fish" or "meat" meant non-veg, so a paneer dish could never be marked otherwise, and "Fish Tikka Masala" filtered wrongly). The "BESTSELLER" tag, which was shown on any dish whose name contained "tikka" or "biryani", is gone: it was not true.
- A menu with no dishes yet says "The menu is being set up" instead of "You've reached the end of the menu".
- The diet filters, the status pill and each option are buttons now, so a keyboard or a screen reader reaches them. Money is written the one way (`core/money.js`).
- `public_base.html` gained `icon` and `head` blocks for a page's own icon and meta tags; the ratchet counts structured data (`application/ld+json`) as data, like `application/json`. 72 templates are left in it.

### Tests
- `menu/tests/test_qr_menu_browser.py` (Playwright, 6 tests, as a guest on a phone): a dish with a required option (said in the sheet), the cart and its GST, placing the order, a second round on the same bill (each with its own request id), the status pill; filters and search; calling a waiter; clearing in two taps; dark mode across a reload; a menu with no dishes.
- `menu/tests/test_qr_cart_browser.py` replaces `test_qr_cart_js.py` (which ran the inline script in Node): the same dishes and numbers in Chromium, for GST, liquor VAT, an offer taken off before tax, and no offer row without offers. Its Node harness moved to `core/tests/node_page.py` for the offer setup page, which still has inline code.
- `static/js/tests/qr_menu.test.mjs` (14 tests): the cart, options, placing the order (a second round, no answer, another table's order, a billed table, a refusal, a double tap, blocked storage), every backend stage on the timeline, when a status is finished, the poller, escaping.
- Two test classes in `menu/tests/test_menu.py` read the page's inline script as text; their markup checks stay, their behaviour checks are the tests above.

## 2026-11-13: Money written one way on the billing screens

### Fixed
- **One cart wrote money three ways** (V12): "₹430", "₹20.00" and "₹450" side by side, and the counter's pay sheet said "₹ 264.00". The billing cart, the counter's pay sheet and change, the running order's lines, the token counter's Pay Now and the modifier prices all use one rule now (`static/js/core/money.js`, the same as the dashboard and the server's `core/money.py`): whole rupees when there are no paise ("₹20"), two decimals when there are ("₹13.60").
- The bill page keeps two decimals: it is the invoice, and an invoice prints paise.

### Tests
- The billing screen's browser tests and node tests now expect the one format (GST "₹20", the pay sheet "₹264", change "₹36"). Three browser tests and three node tests fail on the old modules.

## 2026-11-13: The header on a phone is one row

### Fixed
- **On a phone the header's controls wrapped into two to four rows of icons**, eating the screen: the floor plan's header was 321px tall on an 844px phone, the token counter's 274px, billing's 172px. Under 576px wide they now form one row that scrolls sideways (the page itself never does), and clock in and order history step aside (both are on the dashboard). After: floor plan 153px, token counter 134px, billing 123px.

### Tests
- `core/tests/test_phone_header_browser.py` (Playwright, at 390px): billing, the floor plan, the kitchen, the dashboard and the token counter each have one row of header controls, a header under 160px, and no sideways page scroll. Fails on the old stylesheet.

## 2026-11-13: The setup badge no longer covers the billing buttons

### Fixed
- **"Setup 4/5 complete" covered the billing screen's own buttons**: Bill and Print on a laptop, View Order on a phone (seen on the screenshot sheet). The badge sat in the corner of every page until setup was done. It is offered now where an owner sets up: the dashboard, setup and menu pages; never on billing, the bill, the floor plan, the kitchen, the token counter or live orders.

### Tests
- `core/tests/test_setup_checklist_where.py` (3 tests): the pages it shows on and the working screens it never does; billing and the floor plan rendered without it, the dashboard and setup with it.

## 2026-11-13: QR codes drawn here, never fetched from a third party

### Fixed
- **The table QR page handed every table's private menu address to an outside service.** It loaded each QR code as an image from `api.qrserver.com`, with the table's token in the address, and with no internet every code was blank. The printer setup page did the same for its two codes (the app download and the restaurant's login). They are drawn on the page now by the QR library Rasova serves itself (`static/js/core/qr.js`, used by both pages), the way the floor plan already drew a table's code.
- **The guard missed it**: `core/tests/test_static_modules.py` looked only for a list of known CDNs. It now fails on anything a page loads from another host (a script, an image, a stylesheet, a CSS `url()`), while a link the user follows is still fine.
- The table QR page's Print All button no longer uses `onclick`.

### Tests
- `setup/tests/test_qr_codes_browser.py` (Playwright, 2 tests): every table's code is drawn and no request leaves the site; the printer page's two codes too. Both fail on the old pages.
- `static/js/tests/qr.test.mjs` (2 tests): each spot drawn at its size and colours, and a missing library, an error or a throw said in the spot.
- The broadened guard fails on the old pages (two outside images each).

## 2026-11-13: CI runs the browser tests

### Fixed
- **Every browser test skipped in CI** (T2): they need Playwright and Chromium, and CI had neither, so the smoke walk, the page tests of everything moved to modules, offline, printing and the empty states never ran where they matter most. `requirements-test.txt` pins Playwright 1.62.0 (the version on the development laptop) and CI installs Chromium before the suite.
- Expect the CI run to take longer, and watch the first one: it is the first time these tests run on Linux. A failure there blocks the auto-deploy, as it should.

### Tests
- `core/tests/test_ci_runs_browser_tests.py`: CI installs Chromium before the suite runs; the pinned Playwright is the one on this machine. Both fail on the old files.

## 2026-11-13: No test reaches a real outside service

### Fixed
- **A test called Google's AI with the real key** (T3): `core/settings.py` loads `.env`, and the developer laptop's `.env` holds production keys, so a menu-import path without a fake made a real request (a real "503 high demand" and a "429 quota" in the test logs: slow, costs money, fails for reasons that are not ours). The same mechanism is why every test run had to blank the R2 storage keys by hand, or risk writing to the production bucket.
- Under the test runner, `core/settings.py` now blanks the outside-service secrets before anything reads them: R2 storage, the backup bucket, Google and Anthropic AI, Rasova's Razorpay keys, the mail password, Sentry, WhatsApp. A test that needs a key sets its own fake, as the AI tests already did. `export AWS_...=` before a test run is no longer needed.

### Tests
- `core/tests/test_no_real_services.py`: every secret is blank under the test runner; files go to disk, not the bucket; the menu AI is off; no real payment or mail key. Three of four fail on the old settings on a machine whose `.env` has keys. Added to the fast set.

## 2026-11-13: Pinch zoom works again

### Fixed
- **Every app page, the guest QR menu and login blocked pinch zoom** (V13): their viewport said `maximum-scale=1` (and two of them `user-scalable=no`), so a guest or a cashier with weak eyesight could not zoom anything. Removed. The reason it was there (iPhone Safari zooms into an input whose text is under 16px) is handled in `static/css/rasova.css`: on iPhones, inputs are at least 16px.

### Tests
- `core/tests/test_page_zoom.py`: no template blocks pinch zoom; the iPhone input rule is there. Both fail on the old pages.

## 2026-11-13: An old Android app is asked to update

### Changed
- **The Android app tells the server its real version** (B16 in the suggestions). It used to add `RasovaPOS-Android/1.0` to every request whatever it was, so the server could never tell an old app from a new one. From 1.2.1 it adds its real version (`rasova_android/.../AppUserAgent.kt`). The app is 1.2.1 (version code 5); the signed release APK builds, and the download in `public/android/rasova.apk` is unchanged until Rajath has tried it on a phone.
- **An app older than the server needs sees a red banner on every page**: "This Rasova app is out of date", with a "Get the new app" link to the download. It is drawn by the server (`templates/core/_app_update_banner.html`), so it shows even if the page's scripts fail, and it cannot be dismissed: an old app stops printing.
- The minimum is `MINIMUM_ANDROID_APP` in `core/app_version.py`, today `1.0`, which asks no one. Every app before 1.2.1 reports "1.0", so raising it to `1.2.1` asks every older phone to update: do that in the same push that makes 1.2.1 the download (`rasova_android/README.md`, "Asking old apps to update").

### Tests
- `core/tests/test_app_version.py` (7 tests): the version read from the user agent; versions compared as numbers (1.10 is newer than 1.9); today's minimum asks no one; below the minimum the banner shows on billing, the dashboard and the kitchen, with the download link; a new app and a browser see nothing; the download answers.
- `rasova_android/.../AppUserAgentTest.kt` (3 JVM tests): the real version on the end, added once, an odd version name cannot break the header.

## 2026-11-13: An undo for the deploy

### Changed
- **A bad deploy now puts the previous version back by itself** (B1, option B, Rajath's pick). Before anything changes, `deploy.sh` saves what is running in `versions/last/`: the commit, the installed libraries, the hashed static file names, and a database dump taken before migrate. If a step fails (migrate, collectstatic, a restart) or the new version does not answer `/health/` with 200 within 60 seconds, it resets to the saved commit, reinstalls the saved libraries if they differ, puts the saved static file names back, restarts, and exits 1 (the CI deploy job turns red). It used to be fixed only by another push through CI: 15 to 25 minutes.
- **`bash deploy.sh --undo`** does the same by hand, for a version that is up but wrong.
- The undo reads plain files with bash and git only, so a release that cannot even start Python can still be undone.
- **The database is never rolled back.** A new test makes that safe: every migration from 13 Nov on may only add (no removed or renamed fields, models or tables; no new NOT NULL column without a default). Removing something takes two releases; the second is listed with its reason.
- A failed database dump stops the deploy before anything changes.
- **CI runs the `deploy.sh` of the commit being deployed**, from a copy. It used to run the server's old copy, which `git reset` replaced mid-run while bash kept reading the old one, so a change to the script took effect only a deploy later.
- Em dashes are gone from `deploy.sh`. How to use it: `docs/DEPLOY.md`, "Undo".

### Tests
- `core/tests/test_deploy_script.py` (6 tests) runs the real `deploy.sh` against a throwaway git repository with stand-ins for the server's commands: a good deploy saves then moves on (the dump before migrate); a new version that does not come up is replaced by the old one with its libraries and static names; a migrate that fails half way puts the old one back; `--undo` after a good deploy; `--undo` with nothing saved changes nothing; a failed dump stops before anything changes. All six fail on the old script. Writing them found a bug before it shipped: a failed dump was reported as a success, because bash ignores `set -e` inside a function called from an `if`.
- `core/tests/test_migrations_additive.py`: the rule above, on 13 Nov's own migrations and on a made-up one that breaks it. Added to the fast set.
- Not yet done: a rehearsal on the server itself, with Rajath watching.

## 2026-11-13: Central kitchen and stock requisitions removed (release 1)

### Removed
- **Central kitchen** (production batches, dispatch to outlets, receiving by scan) and **stock requisitions** (P2 in the suggestions, decided by Rajath). They were built for chains that cook in one kitchen and send to branches; Rasova sells to single outlets. They were the largest block of phase 2 work no customer needs: five pages with 575 lines of inline code, 1,096 lines of views, 17 addresses.
- Gone: the pages, the addresses, `inventory/central_kitchen_views.py` and `inventory/requisition_views.py`, the dashboard tiles, the inventory board's two links, the "this outlet is the central kitchen" checkbox in outlet settings, and two feature flags (`central_kitchen`, and `barcode_transfer`, which only its receive scanner used).
- The public QSR page no longer offers it: its descriptions, the feature card, the FAQ entry (also in the page's structured data) and the line under "More than one outlet". `docs/MARKET_COMPARISON.md` says it was removed.
- **Kept on purpose for one more release**: the models and their tables (`StockRequisition`, `RequisitionItem`, `ProductionBatch`, `BatchItem`, `BatchTransfer`) and `Outlet.is_central_kitchen`, so the version now live still runs against the database after this deploy. Release 2 drops them, after a check on the server that no restaurant has rows.
- The code as it was is under the git tag `archive/central-kitchen`: `git checkout archive/central-kitchen -- <paths>` brings it back.
- The ratchet is down to 73 templates.

### Tests
- `inventory/tests/test_central_kitchen_removed.py`: the old addresses answer 404, the two flags are unknown, and no screen links to them.
- Removed with the views: `ConvertToPOTests`, `RequisitionOutletScopingTests`, `CentralKitchenOutletScopingTests` (inventory) and `OutletSettingsCentralKitchenTest` (setup). `AutoRouteTests` stays: it tests a model method that stays until release 2.
- The tenant walk no longer maps `req_id`, `batch_id` and `transfer_id` (no address takes them).

## 2026-11-13: The dashboard: today first, tiles by job

### Changed
- **The owner's dashboard opens on today** (V6 in the suggestions): sales, orders, the average bill, tables open and dishes in the kitchen, then anything that needs a look (voids, discounts, low stock), each a link to where it is dealt with. It used to open on a wall of identical tiles with "Today's Performance" at the bottom on a laptop. One block per outlet for an owner with several. It updates every minute and on the refresh button; a failed update keeps the numbers and says they are the last ones.
- **The tiles are grouped by job**: Service (tables, tokens, live orders, covers, kitchen, billing, calls) and Back office (reports first, cash register, history, menu, QR, stock, shifts, reservations, guests, setup), filling the row instead of leaving gaps.
- **Rasova's own tools are in their own group, "Rasova staff"**, seen only by a superuser (a reseller sees Partner). They used to sit among the owner's tiles.
- Who sees which tile is plain data now (`accounts/dashboard_tiles.py`) instead of some forty nested template conditions. The minute's refresh sends the same ready-made blocks as the page, so the screen never works out a rule of its own.
- The page is markup only: its script is `static/js/dashboard/`, its style `static/css/dashboard.css`. The Zomato and Swiggy switches no longer reload the page; New Order (a direct-billing counter) ignores a second tap. The ratchet is down to 78 templates.
- **Money written one way** (the start of V12): `core/money.py` and `static/js/core/money.js` write whole rupees when there are no paise ("₹430"), two decimals when there are ("₹20.50"), with Indian grouping ("₹1,25,000"). The dashboard uses it; the billing cart moves onto it next.

### Tests
- `accounts/tests/test_dashboard.py` (11 tests): the tiles for an owner, a superuser, a reseller, a manager and a cashier; tiles following the features; New Order as an action with its count; today's numbers as words and what needs a look; today before the tiles on the page; no backend link for an owner; the refresh sending the same blocks.
- `accounts/tests/test_dashboard_browser.py` (Playwright, 4 tests): today first and the refresh drawing the server's numbers; the Zomato switch turning on with no reload and saved; the superuser's group; New Order opening a new token's bill.
- `static/js/tests/dashboard.test.mjs` (5 tests), `money.test.mjs` and `core/tests/test_money.py`: one table of money examples read by both copies of the rule.

## 2026-11-13: A new restaurant's first day explains itself

### Changed
- **Every first-day list says what it is for and offers the one button that fills it** (V9 in the suggestions; setup in under 30 minutes is a deal blocker). A survey opened every owner page as a brand-new restaurant (no tables, dishes, staff or orders) and found six that left a new owner stuck:
  - billing showed an empty "Menu" column: now "No dishes yet", with Add dishes;
  - the floor plan said "All clear" with 0 tables: now "No tables yet", with Add tables;
  - menu management showed only a GST note: now "No dishes yet", with Add sections and a word about AI Import;
  - the table QR page was completely blank: now "No tables yet", with Add tables;
  - the cash register showed a table header and nothing else: now "No register sessions yet", with Open register (the page's own sheet);
  - the rota said "add staff first" with no link: now with Add staff.
- One shared empty state: `templates/core/_empty_state.html` for a template, `emptyStateHtml()` in `static/js/core/empty_state.js` for a list a script draws (the floor plan). The rules are in `docs/FRONTEND.md`.
- Not in this change: the guest QR menu still says "You've reached the end of the menu" when there are no dishes. Its script is inline and it is the next page to move to modules; its empty state goes in with that.

### Tests
- `core/tests/test_empty_states.py` (Playwright, 4 tests): the six first-day pages each show their empty state, and each button opens a page that answers 200; Open register opens the sheet; no owner page shows a new restaurant nothing at all; the include escapes and has one button. Three fail on the old pages.
- `static/js/tests/empty_state.test.mjs` (3 tests): escaping, no button without an address, and a floor with no tables offering to add them instead of saying "All clear".

## 2026-11-13: Printing tells the cashier its state

### Changed
- **A printer chip on the billing, floor plan and token screens** says whether printing works and, when tapped, the one thing to do: "Printer ready", "Printing 2", "Print app not seen for 10 min" (open the Rasova app on the counter phone), "3 waiting to print (7 min)" (the app is connected but the printer is not taking jobs: power, paper, Wi-Fi), "Last print failed" (with the agent's reason), or, with no print app ever connected, "No print app connected" ("Prints from the bill" on an iPhone). It used to be that the cashier found out when no paper came out.
- **The server knows when a print agent last asked for jobs** (`Outlet.print_agent_seen_at`, migration `tenants 0042`). The agent's poll records it at most once a minute behind a cache gate, so an idle poll still costs one cache read.
- The printing state rides on the existing 8-second notifications answer (`printing.services.status.printing_state`): no new poll. The words are `static/js/core/printing_status.js`.
- **Setup's Test Print waits for the printer.** It used to say "Test queued" the moment the server accepted the job, which proved nothing. It now asks for the job (`/printing/job/<id>/`, owner and manager, own outlet only) until an agent finishes it: "Printed", "The printer refused it: <reason>", or, after 20 seconds, which of the app or the printer to check. Its script is a module now (`static/js/setup/`).
- No WebSocket probe of a desktop agent: the current printing model is a print app polling the server, and a probe would log a browser error on every billing screen without a desktop agent.

### Tests
- `printing/test_printing_status.py` (11 tests): the agent's poll recorded once a minute, an idle poll counts, a wrong key records nothing; the state for one outlet and no other (waiting, oldest, failures in the last hour, the last error); the notifications answer carries the user's own outlet; the job status for a manager, 404 for another outlet or restaurant, 403 for a waiter; Test Print answers with the job to wait on.
- `printing/test_printing_browser.py` (Playwright, 6 tests): the chip ready, red with what to open (and its hint on a tap), red for stuck bills on the floor plan, grey with no app; Test Print showing "Printed" only once a stand-in agent finished the job, and the agent's reason when it refused.
- `static/js/tests/printing_status.test.mjs` (16 tests): every state's words, the chip drawing and hiding, Test Print's waits, a dropped connection, no IP, a refusal, a double tap.
- The tenant walk covers the new job status address (another restaurant's job is refused).

## 2026-11-13: One header for every page

### Changed
- **`base.html` draws the header for every page** (V2 in the suggestions). 56 pages used to write their own: 32 had their own theme button (by `onclick`), about 22 had none, most had no logout, billing had two, and the name and subtitle were written a dozen ways (some with em dashes). Now every page has, in the same order: the brand (to the dashboard), the subtitle "Page · Outlet", clock in/out, the page's own links, the alert badges, order history, the theme switch and logout.
- A page fills only its slots: `page_title` (a breadcrumb may link its parent: "Inventory / Variance"), `page_outlet`, `header_links`, `header_badges` (billing, tables and live orders keep the wide badges) and `header_theme` (only the kitchen, for its own light switch). The rules are in `docs/FRONTEND.md`.
- 60 pages moved in one change: once the base drops the old `header_left` and `header_right` blocks, Django silently ignores a page still using them, so a half-moved state would have lost buttons with no error.
- 32 `onclick` theme buttons are gone (the ratchet's counts moved by exactly those).
- The billing subtitle is readable in dark mode again (it was dark grey on black).

### Tests
- `core/tests/test_one_header.py` (9 tests): no template uses the old blocks; no page repeats the theme switch, logout or badges in its own slots; no em dash in a title; the subtitle is page then outlet; a breadcrumb links its parent; every part once on billing, tables, reports, setup, kitchen and tokens; the kitchen keeps its switch; billing keeps its wide badges; no template comment shows on the page. Four fail on the old templates.
- The smoke walk checks the header on every owner page: one brand link, one theme switch, one logout, none by `onclick`. On the old templates it finds 110 problems across the pages.
- Two older tests read the old header (the reports page's `onclick` button, the floor plan's old subtitle) and now read the new one.
- Full suite: 2,398 tests in 5.8 minutes.

## 2026-11-13: A screenshot sheet to look at before a push

### Added (test tooling)
- **`SCREENSHOTS=1 python manage.py test core.tests.test_screenshot_sheet`** makes one page of forty pictures: ten screens (login, dashboard, billing, bill, floor plan, kitchen, QR menu, token counter, reports, setup) in light and dark, at desktop and phone width, with any page error written under its picture. It is written to `md_files/screens/` (not in git) in about 80 seconds. It is not a pixel test: it is for a person to scroll through before a push, so a broken look is seen before a customer sees it. Without `SCREENSHOTS=1` it is skipped, so the suite does not spend the time.
- Its first run showed three things to fix: the "Setup 4/5 complete" badge covers the billing screen's bottom buttons, the billing subtitle is nearly invisible in dark mode, and the dashboard subtitle still has an em dash.

### Tests
- The page-building half always runs: one row per screen, a problem under its picture and escaped, ten distinct screens.

## 2026-11-13: /health/ checks what takes a small server down

### Changed
- **`/health/` has a detailed answer for a monitor or a superuser** (`core/health.py`). It used to check the database and nothing else, so a stopped Celery worker, a full disk, full swap, a backup that quietly stopped, or a dead print agent all said "healthy". The detail checks each: Redis, the worker and beat (beat queues `core.tasks.heartbeat` every minute and its result row proves both ran), free disk (under 10% fails), swap (over 80% used fails), the last good backup (older than 26 hours fails), and the oldest print job not yet printed (10 minutes fails). One failure makes the answer 503.
- Everyone else, a load balancer included, gets the same one-line answer as before. The detail needs a superuser login or the `HEALTH_TOKEN` from the server's environment in an `X-Health-Token` header; with no token set, only a superuser sees it. No exception text, host or restaurant name is ever in the answer.
- `scripts/backup/backup_to_r2.py` writes `logs/last_backup_ok` after a confirmed upload, which is what the backup check reads.
- Setting up the monitor: `docs/DEPLOY.md`, "Health detail for an outside monitor".

### Tests
- `core/tests/test_health.py` (16 tests): who sees the detail and who does not, a healthy server check by check, a stopped heartbeat, a heartbeat never seen, an old or missing backup, a nearly full disk, swap read from /proc/meminfo, a stuck print job (a finished one ignored), a broken check failing without showing why, no restaurant named, the heartbeat scheduled every minute, and the backup marker written only after the upload. Eight fail on the old view.

## 2026-11-13: A fast test set, and a test suite that is quicker everywhere

The full suite took about 30 minutes, so it was not run before a commit. There is now a set for that.

### Changed
- **`python -m core.fast_tests` runs the tests a commit must never break, in about 90 seconds** (425 tests): the tax engine and its browser copy, bills, payments, discounts, offers, promos, bill numbers, an order made once, the tenant isolation walk, and the frontend guards with the node module tests. It runs as `manage.py test` (so settings sees a test run), eight processes, and blanks the storage keys first so no test can reach the production bucket. `--keepdb` skips rebuilding the test database. The full suite still runs before a push and in CI.
- **Tests hash passwords with Django's fast test hasher** (`core/settings.py`, only under the test runner; production is untouched). The production hasher cost 1.4 seconds per user, and most tests make several: one test class went from 23 seconds to under 1. The fast set went from 7.7 minutes to 84 seconds, and the whole suite gets the same saving. A few test files already switched it off for themselves; those switches are now redundant and harmless.

### Tests
- `core/tests/test_fast_tests.py`: every label in the set still names tests; no label twice; no browser test and no thread-race class slips in (they belong in the full run); the guards the set exists for are in it; it runs as `manage.py test` with the storage keys blanked.
- `core/tests/test_test_speed.py`: the suite uses the fast hasher, a password still checks as one, and a hundred hashes take under a second. Fails on the old settings.

## 2026-11-13: An order taken offline can never be made twice

Offline mode is one of the three deal blockers. Its promise is that an order taken with no internet reaches the kitchen once the connection is back, exactly once. This closes the one way it could arrive twice.

### Fixed
- **An order whose answer was lost on the way back was made a second time.** When the server took an order and the connection dropped before its answer reached the tablet, the tablet could not know: it kept the order and sent it again once the connection was back. A walk-in became two orders; a table got every dish twice on its bill. Two tabs of the same device replaying the same saved order at the same moment did the same.
- Every order request now carries its own id (`request_id`, a UUID made by `static/js/core/request_id.js`). The server remembers the ids it has taken (`orders.OrderRequest`, migration `0072`) and answers a repeat with the first order, changing nothing. The row is written in the same transaction as the dishes, so a refused order leaves nothing behind and can be sent again. A unique index, not the view, stops two requests that arrive at the same moment.
- The id is fixed when the cashier taps Dispatch, Checkout or Pay and Print, and the copy saved on the device keeps it. Tapping again after "Network error" sends the same id; a changed cart, or the next customer's order, gets a new one.
- A guest is only answered about the table they scanned, so an id cannot be used to reach another table's order.
- A request with no id works as it always did.

### Not in this change
- The guest QR menu does not send an id yet. Its script is still inline and is the next page to move to modules; the server already accepts an id from a guest.
- A cash payment saved offline whose answer was lost is still sent again. The server refuses it (the order is already paid), so no money is counted twice, but the cashier sees "could not be synced" for a payment that did go through.

### Tests
- `orders/tests/test_order_request_id.py` (12 tests): a walk-in and a table order sent twice, a second round with its own id, no id, a refused request, an id that is not one, another restaurant with the same id, a guest twice, a guest at another table, token numbers, and six real threads sending the same request at the same moment. Nine fail on the old view.
- `orders/tests/test_offline_order_browser.py` (Playwright, 2 tests): the billing screen with the browser's network cut takes an order, the network comes back, and the database has exactly one order, sent to the kitchen. And the lost answer itself: the server takes the order, the test cuts the connection before the answer, the saved copy is replayed. The old code makes two orders.
- `static/js/tests/request_id.test.mjs` (17 tests): the id from each kind of browser, the same request keeping its id, a new request never sharing one, the offline queue replaying with the id it stored, two tabs.

## 2026-11-13: The frontend rebuild, phase 2: the kitchen display

The kitchen display's 151 lines of inline script, 187 lines of inline style and 8 `onclick` attributes are modules and a stylesheet now, tested in node and in a real browser.

### Changed
- **The kitchen display is markup only** (`kitchen/templates/kitchen/kitchen.html`): no inline script, no `<style>` block, no `onclick`, no `style=` attribute. Its code is `static/js/kitchen/`: `board.js` (the tickets as markup), `loader.js` (the 5-second refresh), `actions.js` (start, ready, bump, clear all), `light.js` (the kitchen's own light switch), `index.js`, and `boot.js`, a plain script in the head that applies the light or dark choice before the first paint. Its style is `static/css/kitchen.css`.
- **The page's addresses reach the script in one block** (`kitchen.views.kitchen_page_data`), reversed on the server. No address is typed in the script.
- A refused tap (a dish someone else already moved on, a lost connection) is said in a toast. It used to be written to the browser console, where no cook looks.
- A second tap on the same dish while the first is on its way is ignored.
- The board is redrawn only when something on it changed, and only from the newest answer.

### Fixed
- **A station with an apostrophe in its name ("Chef's Grill") could not be picked.** The name was written into an `onclick` attribute, where the apostrophe ended the text early and the button became a syntax error. The name is a `data-station` attribute now.
- **A refused refresh showed "All Caught Up".** When the server answered the 5-second refresh with an error, the screen said there were no orders. It says "Sync Failed" with the server's words and keeps trying.
- **Picking a station while a refresh was on its way did nothing** until the next refresh 5 seconds later, and the old station's tickets stayed under the new station's tab. The pick asks at once, and the older answer is dropped.
- With the kitchen's light switch on, the browser's own scrollbars and form controls stayed dark. They follow the switch.

### Tests
- `static/js/tests/kitchen.test.mjs` (21 tests): what a ticket shows, escaping, each tap's address, a refusal, the connection down, a late answer, a double tap, clear all answered No, the light switch with and without storage.
- `kitchen/test_kitchen_browser.py` (Playwright, 5 tests): a dish from sent to cooking to ready, bump, the apostrophe station, the owner clearing the screen after being asked (the chef has no such button), the light switch surviving a reload. Each reads the database.
- The ratchet (`core/tests/test_frontend_hygiene.py`) is down to 79 templates.

## 2026-11-13: The browser smoke walk

### Tests
- **Every page the owner can open is opened in a real browser** (`core/tests/test_browser_smoke_walk.py`, Playwright). The URL list is the tenant walk's: every named route, its ids filled from a fully set-up fine-dining restaurant's own rows; a route is a page when it answers the owner's GET with 200 and HTML (67 pages today). A page fails on an uncaught error, a console error, a static file that did not load, or stylesheet text among its words. A new page is walked the day its URL is added. About 80 seconds.
- On its first run it found the two breaks fixed in the entries below (live orders, the reports dashboard). It would also have caught both breaks of the entry before them (the QR menu's stylesheet as text, the running order).

## 2026-11-13: The reports dashboard drew no charts

### Fixed
- **The reports dashboard showed empty boxes where its two charts belong.** Its script read `themeManager` while the page was still parsing; `static/js/core/app.js` (a module since phase 0) sets it only once the page is parsed. It threw before either chart was drawn. The script now runs once the page is parsed. Found by the browser smoke walk. It never reached production: it came in with the unpushed phase 0 commit.

### Tests
- `reports/tests/test_dashboard_browser.py` (Playwright): the sales chart is drawn with no error in the browser. Fails on the old template with "themeManager is not defined".

## 2026-11-13: Live orders showed no orders

### Fixed
- **The live orders screen stayed on "Loading" and listed nothing.** Its script asked for the orders while the page was still parsing; that reads `OrderEdit`, which `order_edit.js` (a module since phase 1) sets only once the page is parsed. It threw, and the 8-second refresh on the next line never started. The whole script now runs once the page is parsed. Found by the browser smoke walk. It never reached production: it came in with the unpushed phase 1 commit.

### Tests
- `orders/tests/test_live_orders_browser.py` (Playwright): the screen lists the open order and its dish with no error in the browser. Fails on the old template with "OrderEdit is not defined".

## 2026-11-13: The QR menu printed its stylesheet as text

### Fixed
- **The QR menu showed its whole stylesheet as text above the dishes, unstyled.** Phase 0 swapped the Google Fonts links for Rasova's own `vendor/fonts/fonts.css` and the new `<link>` lost its closing `>` in seven templates that do not extend base.html. A browser reads the next tag as more attributes of the link and drops it. On the QR menu that tag was `<style>`; on login and the demo switch it was Bootstrap's icons; on the running order, the order-locked page and inspect it was Bootstrap itself. All seven are closed.
- **The running order showed no dishes and never refreshed.** It called its first refresh while the page was still parsing; the refresh reads `OrderEdit`, which `order_edit.js` (a module since phase 1) sets only once the page is parsed. It threw, and the 3-second refresh on the next line never started. The refresh now starts at `DOMContentLoaded`.
- Neither reached production: both came in with the unpushed phase 0 and phase 1 commits.

### Tests
- `core/tests/test_template_tags_closed.py`: no template leaves a tag open into the next one (Django tags, comments and inline scripts set aside). Fails on the old code with the seven, at their lines.
- `core/tests/test_page_styles_browser.py` (Playwright): the QR menu by table token and from a staff login is styled, with no CSS on the page as text; login loads its icons; the running order loads Bootstrap and lists its dishes with no error in the browser. All four fail on the old templates.

## 2026-11-13: The frontend rebuild, phase 2: the floor plan

The floor plan is the screen a fine-dining cashier lives on. Its 345 lines of inline script, 148 lines of inline style and 28 `onclick` attributes are modules and a stylesheet now, tested in node and in a real browser.

### Changed
- **The floor plan is markup only** (`orders/templates/orders/tables.html`): no inline script, no `<style>` block, no `onclick`, no `style=` attribute. Its code is `static/js/tables/`: `floor.js` (the cards, the alert strip, the numbers and the sheets as markup), `actions.js` (what a tap asks the server to do), `loader.js` (the 5-second refresh), `index.js` (reads the page's data, handles every `data-action`). Its CSS is `static/css/tables.css`.
- **The page's addresses reach the script in one block** (`orders.views.table_views.tables_page_data`), reversed on the server. No address is typed in the script.
- **No browser pop-ups on the floor plan.** Unmerge and Approve All Items asked with the browser's own `confirm()`, which never appears inside the Android app, so on a phone the two silently did nothing. They ask in the page's own dialog now.
- Messages use the same toast as every other screen; the page's own toast box is gone. After combining, splitting or moving tables the floor is redrawn in place, where the whole page used to reload a second later.
- Every name a person typed (a table, a section, a waiter) is escaped where it is drawn. A table named with `<` or a quote used to be put into the page as HTML.
- "Floor Plan: Main" in the header, without the em dash.

### Fixed
- **The Transfer button in the header did nothing.** Its sheet showed the source table as an empty, disabled picker, and the order to move was only ever set from a table's More menu, so Execute Transfer returned without a word. The source can be picked now (the tables that have an open order), and the order moved is the one sitting at it.
- **A name being typed in the Tables sheet was wiped every 5 seconds.** The refresh redrew the list of tables, and the pickers in Combine and Transfer, whether or not anything had changed. They are redrawn only when the tables, their names, sections or free state change; the cards likewise only when what they show changes.
- **A table's QR was drawn by api.qrserver.com.** That site was sent the table's menu link with its token, and the QR was blank without internet. It is drawn on the page by the QR library Rasova already serves. View Large opens that same image in a new tab.
- **An old answer could put the floor back.** The floor is asked for every 5 seconds and after every action; an answer to the earlier question arriving after the later one was drawn over it (a table just cleaned showing dirty again until the next refresh). Only the newest question's answer is drawn.
- **A second tap on Bill, Mark Clean, Execute Merge or Execute Transfer sent the request twice.** A second tap is ignored until the first answers.
- Combine and Transfer with a picker left empty say what is missing; they used to do nothing. A failed load says why (the server's message, a lost session, no connection) in place of a JSON parse error.

### Tests
- `static/js/tests/tables.test.mjs` (29 tests): the numbers and the alert strip; each kind of table's buttons; sections; escaping; the More sheet; which tables an order may move from and to; the sheets left alone while nothing changed; the QR link; the loader (a failure's reason, an old answer arriving late, a late failure); every action (what it sends, a refusal, offline, a double tap, asking before Unmerge and Approve).
- `orders/tests/test_tables_browser.py` (Playwright, 11 tests, each ending in the database): the floor drawn with its sections and numbers; a free table starting an order; Bill; Mark Clean; an order moved from the header's Transfer and from a table's own sheet; two tables combined, and split only after the question is answered; a guest's QR order approved; a name typed across a refresh and saved; a table added; a waiter refused and told; a table's QR drawn on the page with no request to another host.
- The ratchet: `tables.html` is gone from the baseline (80 templates left).

## 2026-11-13: The frontend rebuild, phase 2 begins: the bill page

The bill page is where money changes hands: discounts, the UPI QR, cash, card, Razorpay, the cover, loyalty points, printing. Its 541 lines of inline script and 132 lines of inline style are modules and a stylesheet now, tested in node and in a real browser.

### Changed
- **The bill page is markup only** (`orders/templates/orders/bill.html`): no inline script, no `<style>` block, no `onclick`, no `style=` attribute. Its code is `static/js/bill/`: `index.js` (reads the page's data, handles every `data-action`), `amount.js` (change due, split share, the UPI link), `discounts.js` (what is checked before asking the server), `payment.js` (taking a payment, offline cash, opening the register), `bill_print.js` (where the bill is printed after a payment), `razorpay_qr.js` (the QR sheet and its polling), `dialogs.js` (the small forms). Its CSS is `static/css/bill.css`; the receipt roll's width for browser printing is `static/css/print/paper_58.css` or `paper_80.css`.
- **The page's data reaches the script in one block** (`orders.views.billing_core.bill_page_data`): the balance, the UPI id while something is left to pay, the printing settings, and every address the script calls, reversed on the server. No address is typed in the script.
- **No browser pop-ups on the bill page.** Split, closing a bill without payment, and opening the cash register from the bill used the browser's own `prompt()` and `confirm()`. Those never appear inside the Android app, so on a phone the three silently did nothing. They are themed forms now, each saying what is wrong when the input is not right (a split into 0 people, a missing reason).
- Text the server sends back (a guest's name from loyalty lookup) is escaped where it is drawn.

### Fixed
- **A restaurant name with "&" broke the UPI QR.** The name went into the payment link as typed, so "Salt & Pepper" cut the link at the ampersand: the UPI app saw a payee called "Salt " and a stray parameter. The name is encoded now.
- **A second tap on a payment button could record the payment twice.** Nothing stopped two requests while the first was on its way; a second tap is now ignored until the first answers.
- **The bill PDF fetched its stylesheets from the server's own public address.** WeasyPrint loads what a page links, over HTTP, from the address it is given: on the production box that meant out through Cloudflare and back, slow on a small server, and an unstyled PDF whenever that failed. It would also have fetched any address a page named. `core/pdf.py` reads Rasova's static files from disk, lets only the restaurant's logo on R2 go to the network, and refuses everything else.

### Tests
- `static/js/tests/bill.test.mjs` (27 tests): change due, split shares, the UPI link with an ampersand in the name; discount and promo checks; a payment (recorded and printed, reloaded for the receipt page when no printer took it, offline cash queued and UPI refused, a dropped connection, a device that cannot save, no register open then opened once and the payment retried once, a double tap); printing (Android's queue, the queue refusing, the PC agent, no agent, split bills, nothing printed, the Print button, a blocked popup); the Razorpay sheet (the split amount sent, paid, expired by the clock and by the server, a failed check that keeps asking, a QR that cannot be made, Regenerate).
- `orders/tests/test_bill_browser.py` (Playwright): the UPI QR's text read after typing an amount, Pay All, a refused split and a split into 3, with one QR on screen each time; change due; no UPI id, no QR; cash taken on a fine-dining bill with no register open (refused, the register opened on the page with its float, the same payment retried and recorded); a cashier held on an unpaid bill; closing without payment refused without a reason and recorded with one. The browser helper now names the address of each failed request, so a test lists the refusals it sets up and nothing else may appear.
- `orders/tests/test_bill_qr_amount_sync.py` read the old inline script as text, because nothing could run it ("verified manually"). The browser test above now checks that behaviour for real; this file keeps the server's side (the balance and UPI id in the page data only when there is something to collect, the QR library loaded only then, no script of the page's own, only a cashier held on an unpaid bill).
- `core/tests/test_pdf.py`: what a PDF may read (static files from disk on any host, a collected hashed file, a logo on R2 or on disk, data URIs) and what it may not (`../`, the dashboard, the cloud metadata address, `file:`, another host). The full render, with the network patched to fail, runs where WeasyPrint's system libraries are installed (the server, CI).
- The ratchet: `bill.html` is gone from the baseline.
- **Browser tests no longer leave a request running while the test database is emptied.** Closing the browser does not stop a request it already sent, and the token counter asks the server for its order every 5 seconds. In a full parallel run one such request was still reading the tables when the test's cleanup emptied them, and Postgres deadlocked the two. `core/tests/browser.py` now waits, after closing the browser, until no request of the test server is still working in the database.
- The browser helper's waits give up after 15 seconds, not 5. A wait ends the moment what it waits for is there, so a passing test is no slower; a second full parallel run took 97 minutes instead of about 30, and a step of three requests in a row needed more than 5 seconds there, though every request succeeded.
- `core/tests/test_pdf.py` tells the real WeasyPrint from the stand-in that `billing/tests.py` and the inventory tests put in place on machines without its system libraries (the real `HTML` is a class, the stand-in's a function), whichever file loads first.

## 2026-11-13: Light mode had a black page behind every screen

### Fixed
- **In light mode every page except billing was white cards on a black page, with the section headings unreadable, and it came back after going to another screen and back.** Three causes, all fixed:
  - Phase 0 moved base.html's inline styles into `static/css/rasova.css`. One of them, a black background on `<html>` and `<body>` to avoid a white flash before the stylesheets load, used to sit before the theme's stylesheet, where the theme replaced it; in `rasova.css` it came after the theme and won. Removed.
  - `<html>` itself was painted black by a style attribute in base.html, in both modes, so anything `<body>` did not cover showed black. The attribute is gone: the theme (`static/css/themes/luxury.css`) now paints `<html>` as well as `<body>`, from the same `--bg-color`, in both modes.
  - The service worker answered every static file from its saved copy first, so a stylesheet fixed on the server kept arriving as it was before on every normal page load; only a hard refresh showed the fix. Static files whose name does not carry a hash of their content (all of them on a development machine) now come from the server, with the saved copy kept for when there is no network. A hashed name, which can never change, is still served from the cache. Cache `rasova-v6`.
- **The browser's own colours follow the theme.** base.html told the browser the page was dark (`color-scheme`, and the address bar colour) in both modes, so a light page had dark scrollbars and form controls and a black flash before the stylesheets arrived. It now says light, the boot script switches both to dark before the first paint in dark mode, and the theme toggle keeps them in step (`static/js/core/theme.js`).
- Billing looked right only because its panels paint their own backgrounds. None of this reached production: phase 0 has not been pushed.

### Tests
- `core/tests/test_theme_browser.py` (Playwright) uses the app the way a person does: a screen in light mode, another screen, Back to the first, the toggle to dark, another screen and Back again, the toggle to light. At every step it reads the colour of `<html>` and `<body>`, the colour-scheme and address-bar colour the browser was given, and that no colour is fixed on `<html>` in the markup. A second test puts an old copy of a stylesheet in the service worker's cache and checks the page gets the server's file. Both fail on the code before this fix; the first version of this test loaded one page once in a fresh browser, which is how causes two and three got through it.

## 2026-11-12: The frontend rebuild, phase 1: the billing screens

The dine-in billing screen and the token counter were two pages with about 800 lines of inline script each, mostly the same code written twice. They are one module now, tested in node and in a real browser. A cashier sees the same screens; the fixes below are what the tests found on the way.

### Changed
- **One script for both billing screens** (`static/js/billing/`): `index.js` reads the page's data and handles every tap by `data-action`; `cart.js` (the cart and its totals), `dispatch.js` (create the order, the kitchen, the bill, the offline queue), `counter.js` (pay and print in one go), `parcel.js`, `running_order.js` (the Active panel), `menu.js` (categories, search, the item sheet), `printing.js`, `tokens.js` (switching tokens, large text, the drag handles), `ui.js` (what is drawn). `orders/billing.html` and `tokens/token_billing.html` hold markup only: no inline script, no `<style>` block, no `onclick`, no `style=` attribute. Their CSS is `static/css/billing.css`, `token_billing.css` and `billing_parts.css` (what the script draws, shared by both).
- **The page's data reaches the script in one block** (`orders.views.billing_core.billing_page_data`, rendered as `page-data`): the mode (`dine_in`, `counter`, `token`), the outlet's tax settings, and every address the script calls, reversed on the server with 0 standing in for the id. No template tag is left inside JavaScript on these pages, and no address is typed by hand in a script.
- **The four engines are ES modules** (`cart_tax.js`, `offers.js`, `order_edit.js`, `approval.js`): named exports for modules, and still `window.RasovaCartTax`, `window.RasovaOffers`, `window.OrderEdit` and `window.RasovaApproval` for the five pages that have inline scripts left (the QR menu, the running order, live orders, the token dashboard, offer setup). Those pages now read them inside `DOMContentLoaded`, since a module runs after the page is parsed; that added a few wrapper lines to four of them, so their ratchet entries went up by 1 to 6 lines.
- **Dish names, notes and modifier names are escaped** wherever the script draws them (the cart, the Active panel, the item sheet). They were pasted into HTML as typed.
- **If the kitchen cannot be reached after the order was created**, the cart now clears and the cashier is told "Order saved, but the kitchen was not reached". Before, the cart stayed full with no message, and sending it again added every dish to the order a second time.
- The parcel button on the dine-in screen says how the outlet charges ("Add Parcel ₹10/item" or "₹10 flat"), as the token counter's always did, and on both screens it shows what the order on the server really carries.
- A header line read "POS Billing" then a long dash then the outlet; it is a middle dot now.

### Fixed
- **The cart showed a smaller total than the bill when a parcel charge was on.** The server charges the outlet's rate on every item (or a dish's own parcel charge), but the cart added the rate once: two items at ₹10 a parcel showed ₹252 on the cart and ₹264 on the payment sheet. The cart now works the charge out by the server's rule (`parcel.js`), and once the order exists it shows the server's own amount.
- **"Add Parcel" did nothing on a new token.** A token order exists before anything is on it, and the server charges a parcel by the items, so switching it on for an empty token came back as zero and the button went off again. The wish is now kept on the screen and applied at Checkout, after the items are on the order.
- **On an iPhone, paying at the QSR counter opened a page that does not exist** (`/orders/bill/<id>/`) instead of the bill to print. The address comes from the server now (`/bill/<id>/`).
- **Tapping "Add to Order" while the item sheet was still sliding in added the dish but left the sheet open.** Bootstrap ignores a close during the opening animation; the sheet now closes as soon as it has finished opening.
- **A restaurant without the modifiers feature made a request that was refused (403) on every dish tap.** The screen is told whether the outlet has modifiers and does not ask when it has none.
- **A dish shown twice on the token counter** (under Popular and under its category) had two elements with the same id, so only the first card showed the count badge. Both cards carry it now.
- **A device that had installed Rasova but not yet opened the billing screen got a dead page when the connection dropped.** The service worker kept the billing page's HTML at install, but not the files the page loads; since phase 0 that already left out `core/app.js` (the server client, the offline queues), and with the billing script in files it would have left out all of it. Its fixed list of files to keep also named them without the hash production serves them under, so it matched nothing a page asks for. At install the service worker (`static/sw.js`, cache `rasova-v5`) now reads each page it keeps for its scripts and stylesheets, each script for the modules it imports and each stylesheet for its fonts, and keeps them all.
- **Installed while logged out, the service worker kept the login page under the billing page's address**, so a cashier whose connection dropped would have been shown a login form. A page that answers with a redirect is no longer kept.
- The billing page carried a second element with the id `offline-banner` that could never show (base.html's is the one the script finds). Removed; the banner looks the same.

### Tests
- `static/js/tests/billing.test.mjs` and `cart.test.mjs` (46 tests): the cart's merging and totals against the tax engine; the parcel rule (per item, flat, a dish's own, the server's amount once it has spoken); dispatch (the order of calls, a refusal shown and never queued, offline and a dropped connection queued, the kitchen failing after the order exists never queued, a double tap); checkout; the counter (the sheet, change due, paid and printed, the receipt page when no agent answers, offline cash queued and UPI refused, a dropped connection, cancel allowed and refused); printing on iPhone, Android and desktop; the Active panel (a stale answer thrown away, the retry, escaping, approve and reject before checkout).
- **The billing screens in a real browser** (`orders/tests/test_billing_browser.py`, on Playwright like the three browser tests already here): Chromium loads the page from a live test server and taps through it, and the test then reads the order from the database. Dine-in: menu to cart to table to Dispatch to the Active panel. A phone: the cart page opens and closes. The counter: a parcel charge, the payment sheet showing the server's total equal to the cart's, cash, change, paid. The token counter: one-tap adds with badges, a parcel wish that outlives a poll, Checkout to the bill; and switching tokens without a reload. Any uncaught error, `console.error` or failed request in the browser fails the test. `core/tests/browser.py` holds the shared part (`BrowserTestCase.run_page`: log in, open a page, run steps in it, collect the errors) for the next page that is migrated. Skipped where Playwright is not installed.
- The same browser test file checks the offline promise: after the app installs from the dashboard, every file the billing page loads (followed import by import) is in the service worker's cache without the page ever having been opened. On the old service worker it names the missing ones.
- **The payment race tests failed now and then on a busy machine, and not because of the code they test** (`orders/tests/test_issued_bill_guard.py`). They hold an order's lock, start a request in a thread and wait until Postgres shows it waiting. But Postgres freezes what `pg_stat_activity` shows for the length of a transaction, at the first look: if the test looked before the request's connection existed, the request stayed invisible however long it waited (in one run it waited 89 seconds, unseen). The watch now drops that snapshot before every look, and a new test makes the first look come first on purpose, which fails every time on the old watch.
- `printing/tests.py`: the test that the agent key never travels in an address still looked for it in base.html's inline script, which phase 0 moved to modules, so it had been failing since. It checks the page's data block and the modules now, and that no script builds an agent address with a value in it.
- `menu/tests/test_qr_cart_js.py` and `setup/tests/test_offer_setup.py` run the page's scripts in node in the browser's order (plain scripts, then modules, then `DOMContentLoaded`), so an inline script that reaches for a module too early fails there.
- `orders/tests/test_cart_wiring.py` checks the POS and the token counter by their `page-data` block and the data on each menu card.
- The ratchet (`core/tests/test_frontend_hygiene.py`): `billing.html` and `token_billing.html` are at zero and gone from the baseline (they held 1,526 script lines, 667 style lines and 65 `onclick` between them).

## 2026-11-12: The frontend rebuild, phase 0: the foundation

The plan: `md_files/rasova_step3_frontend_plan_2026-11-11.html`. This phase changes nothing a cashier sees; it changes where the code lives and what keeps it there.

### Changed
- **Rasova serves its own fonts, Bootstrap, icons, SweetAlert2 and Chart.js** (`static/vendor/`, versions and SHA-256 of every file in `VENDOR.md`). Until now the billing page needed jsdelivr and Google Fonts to render at all, so a slow or dead internet connection in the restaurant meant an unstyled page; the reports dashboard even loaded an unpinned `chart.js`. No template references an outside host any more, and a test keeps it so. The service worker precaches the local copies.
- **base.html carries no inline code.** Its 870 lines of script are now ES modules in `static/js/core/` (`http.js` the server client, `offline.js` the order and cash-payment queues, `toast.js`, `theme.js`, `notifications.js`, `platform.js`, `pwa.js`, `checklist.js`, `app.js` wiring them), its 199 lines of style are `static/css/rasova.css`, and the three values it needed from the server travel in one `json_script` block (`rasova_page`, from `core.context_processors.frontend`) instead of template tags inside JavaScript. The one inline script left is the three-line dark-mode boot in `<head>`, marked `data-rasova-boot`, which must run before the theme CSS loads. `app.js` still publishes `window.ui`, `window.apiClient`, `window.offlineQueue` and the other shared objects for the pages that have not been turned into modules; that bridge goes when the last one has.
- **Hashed static files now rewrite the modules' imports too** (`core/storage.py`, `RasovaStaticStorage`): `import "./http.js"` inside `app.<hash>.js` points at `http.<hash>.js`. Without it the first deploy of a module would have 404ed on its own imports.
- **A base for pages with no app chrome** (`templates/core/public_base.html`, `static/css/public.css`): the four error pages, the session-refresh page and the suspended page extend it instead of carrying private copies of the same head and 66 lines of CSS each. The refresh page's button is a link to the same address (a GET) rather than `location.reload()`, which would have offered to re-send the failed POST.
- **Seven places read a shared object while the page was still parsing** (five inventory pages took `apiClient.getCsrf()` into a constant; the billing page and the kitchen-stations page read `rasovaPlatform` behind a `typeof` guard that was always false at that moment). They read the CSRF meta tag directly, or work the value out when it is needed.
- `onclick=` attributes in base.html and the demo banner are gone: the header's theme toggle, the setup checklist and the banner's dismiss button use `data-action`, handled by one listener in `app.js`.
- CI installs Node 24 explicitly (the laptop's version), since the module tests and the engine parity tests run through node.

### Fixed
- **Two flushes of the offline queue could run at once.** The guard against that was set after the first `await`, so the flush on page load and the flush on the `online` event (which fires right after a reload on a flaky connection) could both pass it and create an order twice. The guard now goes up before anything is awaited. Found by the new queue tests: the test for it hung on the old ordering.

### Tests
- `static/js/tests/` (22 tests, node's own runner, driven from `core/tests/test_frontend_js.py` so one `manage.py test` runs them): the server client (CSRF and JSON headers, offline refusal, a 400 with a message, a 200 that says `success: false`, an HTML page instead of JSON, fetch failing, the manager-PIN gate); the order queue (replay and kitchen send, stop when the connection drops, drop and tell on a refusal, never re-create when only the kitchen call failed, overlapping flushes); the payment queue (replay, a refusal shown and never retried blindly, an unanswered payment stays queued); the banner; platform detection and the Android print bridge; the CSRF and page-data readers.
- `core/tests/test_frontend_hygiene.py`, the ratchet: counts inline script lines, inline style lines, `onclick=` attributes, third-party hosts and parse-time reads of the shared objects in every template, against a baseline that can only shrink (84 templates today: 7,336 script lines, 6,467 style lines, 398 `onclick`, no third-party host, no parse-time read). A page that gets worse fails; a baseline entry looser than its page fails; a migrated page's entry is lowered with `python -m core.tests.test_frontend_hygiene --write`.
- `core/tests/test_static_modules.py`: collects the static files with the production storage into a temporary directory and checks every import in every collected module resolves to a hashed file, every module parses in node, every literal `{% static %}` path exists, and no template loads from a third-party host.

---

## 2026-11-11: Every URL walked as one restaurant pointed at another's rows

### Security
- **A test now proves no page shows one restaurant another's data.** `core/tests/test_tenant_walk.py` builds two complete restaurants (every kind of row a URL can name: orders, items, payments, refunds, KOTs, tokens, stations, promos, offers, inventory, purchase orders, requisitions, batches, shifts, guests, reservations, expenses, print jobs), marks every text field of restaurant B with a string that appears nowhere else, then opens all 241 non-public URLs as restaurant A's owner with B's ids. A page fails the walk if B's marker is in the body or the redirect, if it crashes, or if it answers anything but 403/404/405 to B's id (a 200 of B's totals has no names in it, so a status alone is a finding). Public pages (login, the guest menu, the token display board, the webhooks, the print agent) are listed with the reason they are public, and a stale entry fails the test. A deliberately leaky view and a quiet 200 are wired in by a second test to prove the detector catches both. First run on 11 Nov: no leak anywhere. The 29 findings were views that answered B's id with a 400 or a 500 before looking the row up, all fixed below.

### Fixed
- **Views that read the body before asking whose row it was.** Another restaurant's id got "Amount required", "Message text is required", "The price is required" or "Phone required" (400) instead of 404, and in a few places a catch-all turned the 404 into "Could not update, please try again" or a 500. Now the row is looked up first, with the restaurant and outlet in the query, and a 404 passes through every catch-all: `pay_order`, `refund_payment`, `split_pay`, `apply_discount`, `apply_item_discount`, `log_bypass`, `send_kitchen_message`, `link_guest_to_order`, `update_price`, `update_menu_item`, `toggle_platform_availability`, `update_station`, `delete_category`, the six modifier views, `add_modifier_recipe`, `restock_item`, `log_wastage`, `adjust_stock`, `rename_table`, `set_default_station` (was a bare `.get()`, so a wrong id was a 500 page), `approve_refund_view` and `reject_refund_view`.
- **`pay_order` answered 500 to a body that wasn't JSON** (logged as "Unexpected error in pay_order"). It is a 400 now, and a JSON list is refused the same way. `orders/tests/test_pay_order_guards.py` (4 tests, 2 fail on the old view).
- **`merge_tables_view` crashed on a missing or malformed body** and the service raised a bare `Exception("Primary table not found")`. The view now says what is missing (400), and a table that isn't this outlet's is a 404 through a new `MergeError`.
- **`download_pdf_bill` imported WeasyPrint before checking whose bill it was.** The lookup comes first, so a wrong id does nothing heavier than one query (and a developer machine without Pango no longer crashes on it).

### Changed
- Any new URL that takes an id must be mapped in the walk's REGISTRY or listed in its PUBLIC_BY_DESIGN with a reason; the walk fails otherwise.

---

## 2026-11-04: PDFs work on rasova.net, and the scheduler survives a reboot

### Fixed (on the server)
- **rasova.net had never been able to make a PDF.** WeasyPrint draws text with Pango, a system library, and Pango was not installed on the server (`OSError: cannot load library 'libpango-1.0-0'`), so every PDF bill, invoice and purchase order failed. Rasova started normally and said nothing. The warning only began to show in the deploy log on 3 Nov, when Rasova started importing WeasyPrint at startup (`refuse_truncated_images`). No one noticed earlier because the only live cafe printed through the phone. Installed on the server by hand on 4 Nov: `libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b fonts-dejavu-core` (the same as the box image). WeasyPrint 70.0 then loaded and made a test PDF with the ₹ sign (DejaVu has it). Memory: measured in the box image, WeasyPrint adds about 26 MB to each Rasova process; after the restart the server had 488 MB available.
- **Beat (the scheduler) didn't come back after a reboot.** The server's worker runs without `-B`, and beat was started with `nohup` by `deploy.sh`, because the `celery-beat` service it tried to restart didn't exist. So after the 3 Nov reboot nothing was scheduled until the next deploy (the demo reset ran 10 times that day instead of 12). Checked that jobs never ran twice: one beat process, and the daily digest ran once a day.

### Changed
- **`systemd/celery-beat.service`** (new): beat as its own service, so it starts on boot and restarts after a crash. Install steps are at the top of the file.
- **`systemd/celery.service`**: no `-B`, matching the server. The repo's copy had it, so copying it to the server next to a separate beat would have run every scheduled job (the 6 am invoices, the 7 am digest) twice.
- **`deploy.sh`** stops before pulling any code if WeasyPrint can't load, and says how to fix it. It installs `systemd/celery-beat.service` whenever the server's copy is missing or differs from the repo's (so a new server needs no manual step), restarts it, stops any second beat (like one left from the old `nohup` start), and prints how many beats run, with a warning unless it is exactly 1. If the service still can't start it falls back to `nohup` as before, with a warning that beat won't survive a reboot.
- **`docs/DEPLOY.md`**: the PDF packages in 2.2, and a new 7.5 on the worker and beat as two services.

### Tests
- `core/tests/test_server_units.py` (new, 5 tests, all fail on the old files): the worker unit has no `-B`; the beat unit runs beat, restarts always and starts on boot; `deploy.sh` restarts the beat service and stops a second beat, and checks WeasyPrint before pulling code; `DEPLOY.md` installs Pango.

---

## 2026-11-04: No address Rasova builds can point to another website

### Security
- **A request could put another website in front of Rasova's addresses.** Code scanning alert #114 (py/url-redirection, medium, open since the cover charge was built on 20 Oct) flagged `covers/views.py`: after a cover is sold, the door page redirected to `f"{request.path}?slip=..."`. Django builds `request.path` from `SCRIPT_NAME` plus the path, and without `FORCE_SCRIPT_NAME` it takes `SCRIPT_NAME` from the request. Tried with Django's test client: with `SCRIPT_NAME=//evil.example` the sale redirected to `//evil.example/covers/?slip=1`, which a browser opens as another website. Changing that one line to `reverse()` was not enough: the same request made `redirect("covers")` (the refusal path) and the login page's `?next=` carry the prefix too, because Django adds `SCRIPT_NAME` to every address it builds. The missing-slash redirect carried it as well (escaped by Django to `/%2Fevil.example/login/`, so on-site but broken).
- **Fix at the root: `FORCE_SCRIPT_NAME = ""`** (`core/settings.py`). Rasova always runs at the root of its address (rasova.net/, a box's http://rasova.local/), so Django now ignores any `SCRIPT_NAME` for every address, everywhere. The cover page also builds its redirect by name (`reverse('covers')`), so it never copies an address from the request. It was the only redirect in Rasova built from `request.path`.
- **How real it was:** low. Only logged-in staff reach that line, through a form with a CSRF token; a browser never adds a `SCRIPT_NAME` header to someone else's request; gunicorn drops headers with underscores unless they come from the same machine, and nginx drops them by default. So the setting closes a door that the servers in front were keeping shut, rather than one that was open.

### Tests
- `core/tests/test_script_name.py` (new): the setting is `""`; the missing-slash redirect and a page that needs a login send the browser to `/login/` and `/login/?next=/dashboard/` whatever `SCRIPT_NAME` the request brings. `covers/tests/test_covers.py`: after a sale, and after a refused sale, the door page sends the browser back to `/covers/`. All four fail on the old code.
- `core/tests/test_image_uploads.py` no longer takes the billing and inventory tests' stand-in `weasyprint` module for the real one. On a PC without WeasyPrint's libraries those tests put an empty module in `sys.modules`; a full run imports them first, so the cut-off-image PDF test ran on the stand-in and failed ("'NoneType' object has no attribute 'write_pdf'") instead of skipping. It now uses WeasyPrint only when the module was loaded from a file. CI and production (Linux, real WeasyPrint) still run the test.

---

## 2026-11-03: Postgres 18 everywhere

### Changed
- **One Postgres major everywhere: 18.** Production already ran 18.6 (Ubuntu 26.04's own package, checked on the server; no upgrade needed) and so did development. CI moves from `postgres:16` to `postgres:18`; its tools step installs the matching 18 client by itself. The box moves from Postgres 15 to 18 while no box is in a restaurant yet: afterwards, every box would have needed its database upgraded on site. `box/docker/compose.yml` uses `postgres:18-bookworm`, and the Rasova image's backup tools are `postgresql-client-18`, from apt.postgresql.org because Debian 12's own repository stops at 15. The dev `docker-compose.yml` uses `postgres:18-alpine`; the README now says PostgreSQL 18.
- **The data mount for Postgres 18 images.** Postgres 18's Docker image keeps its data in `/var/lib/postgresql/18/docker` and refuses to start with the old `/var/lib/postgresql/data` mount. Tried in Docker: with the old mount it stops at once ("in 18+, these Docker images are configured to store database data in a format which is compatible with pg_ctlcluster"); with the volume at `/var/lib/postgresql` it starts as 18.6 and the data survives deleting and recreating the container. Both compose files mount it that way now. A dev volume made by the old 15 setup won't open in 18; start it fresh (`docker compose down -v`).
- `docs/BOX_GUIDE.md`: Postgres 18 in the parts table, the test database (now `rasova-testdb18`, so an older 15 one can stay) and the images to fetch for a bundle.

---

## 2026-11-03: Pillow and pypdf security upgrades; CI's box backup tests use Postgres tools that match the database

### Security
- **Pillow 12.3.0 to 13.0.0 and pypdf 6.19.0 to 6.21.4**, closing all 59 open Dependabot alerts (41 Pillow: 5 critical, 23 high, 13 medium; 18 pypdf: 10 high, 7 medium, 1 low). Every one is about a crafted file: most hang the server or use up its memory, a few leak memory, and the worst Pillow ones (buffer overflows in the JPEG, FlashPix and TIFF code) can run code. In Rasova the way in is a staff upload: Pillow checks and resizes every dish photo and logo (`core/validators.py`) and the AI menu import's photos, and pypdf reads the import's PDFs (`core/ai_service.py`). Only owners and managers can upload, but all restaurants share one server, and the box has the same libraries. `pip check` is clean with weasyprint and python-escpos (which also use Pillow). Rasova's own image and PDF code was run on the old and the new versions with eight image formats (a sideways phone photo, RGBA and palette PNGs, WebP, GIF, BMP, TIFF, CMYK JPEG), four files that must be refused (an SVG with a script and an HTML page renamed `.jpg`, a cut-off photo, an empty file) and three real PDFs: all 28 results identical.

### Fixed
- **A dish photo or logo taken on a phone was stored on its side.** Phones save a portrait photo's pixels sideways and add an EXIF tag saying how to turn them; uploads are re-encoded to WebP, which carries no EXIF, so the tag was lost and the photo showed rotated (a 4000x3000 phone photo was stored 800x600 instead of 600x800). `process_uploaded_image` (`core/validators.py`, used by dish photos and logos) now turns the pixels upright first, as the AI menu import already did.
- **Wherever WeasyPrint loads, a cut-off image was accepted and stored half grey.** WeasyPrint, the first time it is imported, sets `PIL.ImageFile.LOAD_TRUNCATED_IMAGES = True` for the whole process. Rasova imports it at module load in some places and lazily in others, so where WeasyPrint loads (CI, the box) Pillow accepted cut-off images everywhere, uploads included, while on a PC without WeasyPrint's native libraries it refused them. *Corrected 4 Nov: this first said "production" too. rasova.net never loaded WeasyPrint (Pango was missing, see 4 Nov), and a failed import never reaches the line that sets the switch (checked), so production refused cut-off images all along. It would have started accepting them the moment Pango was installed, which this fix prevents.* Found when the new upload test passed on Windows and failed in CI. `refuse_truncated_images()` (`core/validators.py`), called first in `TenantsConfig.ready()`, imports WeasyPrint once so its change happens then (a module's code runs only on its first import) and puts Pillow's default back. Checked on Linux: with the switch off a cut-off photo is refused, and WeasyPrint still makes a PDF that contains a cut-off image (it leaves the image out and logs "Failed to load image").
- **CI failed, so the 2 Nov fix was never deployed.** GitHub's runner image now brings newer Postgres tools than CI's Postgres 16 service. The box's real restore test made a backup with that newer `pg_dump`, which writes `SET transaction_timeout`, a setting Postgres 16 doesn't know, so the restore failed and the deploy job was skipped. A real box was never at risk: `box/docker/Dockerfile` installs `postgresql-client-15` next to `postgres:15`. CI now asks its Postgres service which major version it runs, installs that client and puts it first on PATH (`.github/workflows/ci.yml`), so changing the service image is enough and the tools follow. GitHub's `ubuntu-latest` became Ubuntu 26.04 in late October, whose own repository carries only Postgres 18, so when the needed client isn't there the step adds the PostgreSQL project's apt repository (apt.postgresql.org), which has every supported version. Checked by running the step on Ubuntu 26.04.1: it installed `pg_dump`, `pg_restore` and `psql` 16.15.

### Tests
- `core/tests/test_image_uploads.py` (new; the function had no test): a sideways phone photo comes out upright and turned the right way (checked by where a red edge ends up), through the function and through the real dish and logo saves; a photo without the tag keeps its shape; the result is WebP, at most 800 pixels; and an SVG with a script, an HTML page renamed `.jpg`, a cut-off photo and an empty file are refused. Four of these fail on the old code. Three more: the switch is off after WeasyPrint is imported, `refuse_truncated_images()` undoes it, and (where WeasyPrint's libraries exist) a PDF with a cut-off image is still made. Run on Linux with WeasyPrint working: 387 tests (image uploads, billing, inventory, tenants) pass.
- `box/tests/test_box.py`: the real backup and restore tests skip, with the versions in the reason, whenever `pg_dump`, `pg_restore` or `psql` is a different major version from the database, newer or older (before, only an older `pg_dump` was caught, because it refuses to start). Three tests check that guard itself. With matching tools (Postgres 18 locally) the real backup and restore tests run and pass.

---

## 2026-11-02: Two pages shared the name "dashboard", and a new sales agent couldn't open Rasova

### Fixed
- **`reverse("dashboard")` and `{% url 'dashboard' %}` opened the reports dashboard, not the owner's.** The owner's dashboard (`accounts/urls.py`) and the reports dashboard (`reports/urls.py`) were both named `dashboard`. No app sets `app_name`, so every URL name in Rasova is in one flat list, and when two patterns share a name Django quietly returns the one it meets last: `/reports/dashboard/`. Nothing errors. The reports one is now `reports_dashboard` (the inspection page's "Back to Reports" link uses the new name), and `dashboard` means the owner's dashboard only. The "Dashboard" button on the feature flags page (Rasova staff), which superusers reach from a card on the owner dashboard, now goes back there instead of to the reports dashboard.
- **A new sales agent couldn't use Rasova.** Login sends an agent to `/sales/`. With no restaurants assigned yet, that page redirected to "dashboard", meaning the reports dashboard, which answers 403 to anyone without a restaurant of their own. The sales page's own "No clients attached to your profile yet." message was never shown. An agent with no restaurants now sees the sales page with that message. Anyone else who opens `/sales/` goes to their own home page, the one login sends them to, instead of the reports dashboard (owners and managers) or a 403 (everyone else).

### Tests
- `core/tests/test_url_names.py`: walks the whole URL map, into every `include()`, and fails if a name is used twice in the same namespace, listing the clashing routes. Two more tests prove the check: it catches the 2 Nov clash rebuilt, and it allows the same name in two namespaces.
- The owner-dashboard permission tests (owner in, chef out, waiter out) had been opening the reports dashboard and passing, because both pages turn chefs and waiters away. They now open the owner's dashboard. The owner test needed a dish on the menu (with an empty menu the owner dashboard sends the owner to onboarding) and now checks the page's template as well.
- New: `dashboard` and `reports_dashboard` reverse to the right views; a cashier gets into the owner's dashboard and is sent on to the token screen (the reports dashboard refuses cashiers); the feature flags page's Dashboard button; `/sales/` for an agent with no restaurants, an agent with one, an owner and a waiter; the inspection page's Back to Reports link. Eight of these fail on the old code.

---

## 2026-10-21: The box prints its own print queue, Box Health in Setup, the box guide, and the model diagrams redrawn

### Fixed
- **On a box, Setup's Test print never printed.** It put the job in the print queue for the phone app or PC agent to collect, and a box has neither. A box now prints its own queue: each new job goes to the box's worker, which sends it straight to the printer's IP, and with no worker queue it prints from the request instead, as KOTs do (`box/print_queue.py`). It claims a job the way the agents do (one UPDATE from pending to processing), so a job prints once even if an agent is also pointed at the box; a job left half-done by a worker that died is picked up again after 2 minutes; a printer that doesn't answer fails the job with the reason. The test print now says "the box prints it in a few seconds". In the cloud nothing changes: a job still waits for a device. Checked on a running box against a pretend printer.
- **Box Health was only reachable from a warning banner**, so on a healthy box the owner couldn't find it. It is now a card on the Setup page (owners and managers, on a box only), saying "All good" or "Needs attention".

### Added
- **`docs/BOX_GUIDE.md`**: how to build a release, set up a mini PC, switch it on at a restaurant, make and add licences, update and roll back, back up and restore (and undo a restore), replace a dead box, print, and troubleshoot; the signing keys and how to change one; trying a box on a PC; notes for developers; what isn't done yet.
- **The model diagrams redrawn** (`docs/diagrams/`), last drawn in March with about 25 models, now 74 in 21 apps: the core schema (also left to right and right to left), every model, every model by app, and new: one diagram per app (`docs/diagrams/apps/`), each as PNG and SVG, with a README on reading them. `python manage.py make_model_diagrams` draws them all from the models, so they can't drift again. The abstract base nearly every model inherits is left out, because its arrows buried the real relations, and the PNGs use a 32-colour palette (as sharp, a third of the size).
- The box in the other docs: the README (features, structure, settings, printing, commands, roadmap), `docs/USER_MANUAL.md` (a section for restaurants on a box), `docs/PRINTER_SETUP.md` and `PRINTING_SYSTEM.md` (printing on a box), `docs/MEDIA_AND_BACKUPS.md` (a box's backups), `docs/TESTING_STRATEGY.md` (the compiled run) and `docs/DEPLOY.md` (which runbook is which).

### Tests
- `box/tests/test_box.py`: the box printing its own queue (Setup's test print, no worker queue, printed once with an agent polling too, a job a dead worker dropped, a printer that doesn't answer, the cloud unchanged), the Box Health card, and that every app is in the box build's allowlist (`box/docker/Dockerfile.dockerignore`): a new app left off it would have been missing from every box image.
- `devtools/tests/test_make_model_diagrams.py`: every app gets a diagram, the full one has every model, and the base class stays out.
- Two approval-PIN tests from 20 Oct failed between midnight and 6 am: they asked the void audit for the calendar date, while the audit (rightly) files a night's voids under the business day they started. They now ask for the business day, like the report tests already did.

---

## 2026-10-20: The Rasova box: all of Rasova on one small computer in the restaurant, no internet

### Added
- **A box edition** (`RASOVA_EDITION=box`, the new `box` app). The same Rasova, run on a mini PC on the restaurant's Wi-Fi; tablets and phones open `http://rasova.local`. It prints straight to the printers' IPs (both Celery queues, as in the cloud). What needs the internet is off on a box, whatever the feature switches say: QR ordering, waiter call, guest feedback, Zomato/Swiggy sync, Razorpay, WhatsApp receipts, the menu and recipe importers, email; their pages, the marketing site, the superuser pages and the print agent's paths are 404.
- **First switch-on** (`/box/setup/`): until a restaurant exists every page leads here; it makes the restaurant, outlet and owner (one at a time, under a database lock) and logs the owner in. `tenants/services/restaurant_setup.py` is now the one place a restaurant is made; the superuser's Create Restaurant uses it too.
- **Licence**: a small file signed by Rasova (Ed25519), checked on the box with no internet, tied to the box's machine id. 14-day trial from setup; 14 days' grace after the end date; after that open bills can still be billed and paid but no new order starts (refused at the database, `box/signals.py`). An edited licence, or one copied from a box that is still alive, is refused. **A replacement box** (the old one died, its backup restored) works for 14 days on the old box's licence, which the backups carry, so it opens that night. `python manage.py sign_box_license` makes one on Rasova's PC only; it refuses a box id that isn't 16 hex characters (a box with no machine id shows "unknown", and a licence for that would fit every such box).
- **Clock guard**: a clock that goes back (or jumps months ahead) is flagged on every page until the owner confirms the time.
- **Nightly backup** at 4:30 am to the backup drive: `pg_dump` to a temporary name, read back with `pg_restore --list`, then renamed, so a power cut or a full disk never leaves a fake backup; 14 kept; one at a time; the result recorded and shown. A failed or missing backup, a nearly full disk, a licence near its end and backups on the same drive as the bills are all shown to the owner.
- **Restore** loads the backup into a new database and only then swaps it in by renaming, keeping the replaced one as `<name>_before_restore`: a failed restore changes nothing, and nothing made after the backup is left behind (a table added by an update that was then rolled back used to stay and trip the next try).
- **Box health** page (owner and manager; changes by the owner): licence and box id, licence upload, clock, backups, disk, printers.
- **No source code on a box**: `box/docker/compile.py` compiles every Rasova module with Cython to machine code and deletes the `.py` (only `manage.py`, the `__init__.py` files and the migrations stay readable), and the build fails if any source is left. The test suite runs against the compiled code (Docker target `test`), so what ships is what was tested.
- **Updates**: a signed bundle (`python manage.py make_box_bundle`, Rasova's PC only) on a pen drive. `rasova-box update` copies it to the disk, checks the signature and every file's fingerprint (and has the running version check them again), refuses while bills are open unless told `--now`, backs up, loads, starts, health-checks, and on failure goes back to the old version and the backup by itself. `rasova-box rollback` undoes the last update by hand. The bundle carries the Postgres and Redis images too: the box never downloads anything.
- `box/host/install.sh` (fresh Ubuntu 24.04 mini PC: Docker, `rasova.local`, firewall, no SSH, no sleep, small logs, no surprise updates, the backup drive) and `box/host/rasova-box` (install, status, backup, restore, update, rollback, logs).
- `LOG_DIR` setting: the box keeps its logs in `/data/logs`, outside the image.

### Fixed (found by running the box)
- On a box, signing in sent staff to the restaurant's cloud subdomain (`copper-tap.localhost`), an address that doesn't exist there. A box now keeps them on the address they typed; the cloud is unchanged.
- Links the app builds (the QSR "Now Serving" board) used the cloud's base address; on a box they use the address the device used.

### Tests
- `box/tests/test_box.py` (44): box mode, first run, signing in staying on the box's address, licence states, signing, moved licences, clock, backup logic, a real `pg_dump` backup, real restores into a scratch database (including a damaged backup changing nothing), update bundles (good, changed file, edited manifest, other key, missing pieces, odd names) and the bundle maker, media, status, and the cloud untouched. CI now runs the `box` tests.
- Run for real in Docker: install from the bundle alone, first setup, an order printed to a pretend printer, billed and paid, backup and restore, an update with a database change, a replacement box restored from the backup drive, a deliberately broken update that undid itself, and a manual rollback.
- The whole suite on the compiled code (Docker target `test`): 2,234 tests, 2,196 passed; 22 browser and JavaScript tests skip there (no Chrome or Node in the image), and 16 that read files a box doesn't carry (the marketing site, the print agent installer, the `.py` source) now skip in that build only, by the `RASOVA_COMPILED_TESTS` flag the image sets (`core/testing.py`), so a file missing from the repo still fails CI.

---

## 2026-10-20: For pubs: the nightly bar count, kegs, covers and the liquor register

### Added
- **The nightly bar count** (Inventory, Bar count). Rasova's variance report compares the till's deductions with the recipes, so it can't see a free pour or a drink sold for cash and never rung up; only counting the bottles can. A manager counts each bottle (sealed bottles plus the ml in the open one; bottle size is a new field on the stock item). The count is blind: the screen never shows what the system expects. Rasova then shows what went missing per bottle in ml, 30 ml pegs and rupees (at cost, and at the cheapest way the bottle sells, so it never overstates), and sets the stock to the count. Past counts are listed.
- **Kegs** (Inventory, Kegs). A keg is bar stock with a container of 5 litres or more. "Change keg" records what the finished keg sold through the till against what it holds, takes the difference (foam, spillage, pints with no bill) off the stock, and shows each keg's yield and what the loss was worth.
- **Cover charges** (a new `covers` app, on for pubs). The door sells a cover (amount, guests, cash/UPI/card) and prints a slip with a 5-letter code. On the bill, **Cover** pays as much of what is due as the cover has left, as a payment ("Cover credit"), never a discount, so the bill and its GST don't change. A cover can pay several bills, lapses after its night, and a bill cancelled later gives its part back. Closing a shift now expects the door's cash covers in the drawer (UPI and card covers count as digital). The CA should confirm how to treat a lapsed cover's unused amount.
- **Liquor stock register** (Excel, from Past counts): per business day per bottle, opening + received (with the delivery's reference, for the KSBCL invoice) - sold - broken +/- count correction = closing, in bulk litres, with closing full bottles and loose ml. The official Karnataka layout wasn't found published; the columns are to be matched to a pub's accountant's register.
- `python manage.py seed_pub_demo`: a demo pub (bar station, pegs off 750 ml bottles, a 30 litre keg, a happy hour, covers, a manager with PIN 2580) to record the pub demo on, locally.

### Tests
- `inventory/tests/test_bar_count.py` (14), `inventory/tests/test_kegs.py` (9), `covers/tests/test_covers.py` (13, including two cashiers spending one cover at the same moment), `inventory/tests/test_liquor_register.py` (4), `devtools/tests/test_seed_pub_demo.py`.
- No query per row: saving a count, the past counts page, the keg page and the register each cost the same number of queries for one bottle, count or keg as for many, and tests count them. A count locks the bar's stock in id order, the order every sale locks stock in, so a count and a sale at the same moment can't deadlock. A second "Change keg" within 2 minutes is refused, so a double tap can't record a full keg as lost.
- CI now runs the `covers` and `devtools` tests.

---

## 2026-10-20: A printed bill can't be lowered without a manager, and liquor only in licensed hours

### Fixed (the till leaks found on 19 Oct)
- **Once the bill was printed, a cashier or captain could take money off it on their own.** Proved by running the code: void a dish the guest ate (₹1,000 to ₹0), cut a quantity (₹3,000 to ₹1,000, logged so it never reached the Discount & Void audit), cancel the whole bill, give a bill discount, take the parcel charge off. Now anything that lowers a printed bill (status "billing") needs a manager or the owner doing it, or a manager's **approval PIN** typed on the cashier's screen. One rule for every path, in `orders/services/approvals.py`; the bill page, billing screen, live orders board and running order all ask the same way (`static/js/approval.js`, used by `apiClient` and `OrderEdit`).
- **A captain could cancel a dish the kitchen had already made, as wastage, with no manager.** Cancelling a made dish now needs a manager's approval at any time, not only after the bill.
- **A bar's drinks counted as "probably not made" for 10 minutes after they were sent**, so a bartender could void a poured drink and its stock went back on the shelf. A kitchen station now has **Made as soon as sent** (on by default for a station named "Bar"); a cancel after that is wastage and needs the PIN.
- **Any staff could tag an order as Zomato or Swiggy with no app order ID**, and its GST disappeared from the bill (the app pays it under s.9(5)). An app order now needs the app's own order ID, and re-tagging a printed bill as an app order needs a manager, even when the same save adds a dish.
- **A dish discount or a free dish on a printed bill was refused for everyone, managers included**, while a bill discount was allowed for anyone. Both now follow the same rule: allowed, with a manager's approval.

### Added
- **Setup, Approval PIN**: each manager or owner sets their own 4 to 6 digit PIN after typing their password. It is stored hashed; 5 wrong tries lock it for 15 minutes; easy ones (1111, 1234) are refused. The owner can clear a manager's PIN.
- **Discount & Void audit, "Changed after the bill was printed"**: every approved change with the bill before and after, who made it and whose PIN approved it. Void and discount events also carry `approved_by`.
- **Liquor only in the licensed hours.** Each outlet has liquor hours (10 am to 1 am by default, a Karnataka CL-9 bar's), on Outlet Settings when the outlet sells liquor. A drink added outside them is refused with the hours ("'Pint' is liquor, and liquor can be ordered only from 10:00 am to 1:00 am."), for staff and QR guests alike; food still sells, and drinks ordered in time can still be billed and paid after 1 am. The owner can change the hours or switch the lock off.

### Tests
- `orders/tests/test_printed_bill_approval.py` (33): each leak refused without a PIN (the leak tests fail on the old code: 10 of 10 checked in a separate copy at the previous commit), each change with a PIN recorded, wrong PIN counted, lock and unlock, who may approve (not another outlet's manager, a cashier, a deactivated manager or another restaurant), the PIN page.
- `orders/tests/test_approval_pin_browser.py`: in Chromium, a cashier's dish discount on a printed bill asks for the PIN, the right PIN applies it and the wrong one says "Wrong PIN. 4 tries left." Skips without Playwright.
- `orders/tests/test_liquor_hours.py` (9; 7 fail on the old code, the 2 that pass check what is still allowed).
- The live orders board and a cancel read each line's ticket and station with the line itself (one query, not one per line); a test counts the board's queries with 1 order and with 9.
- Tests that pinned the old rules now say the new ones: a cashier or captain writing off a made dish sends a manager's PIN, and a refusal is the 403 the screen turns into a PIN prompt. Liquor tests that aren't about hours run with the hours lock off, so they pass at any time of night.

---

## 2026-10-20: A missing migration dependency

### Fixed
- `promos.0001_initial` (the state-only move of Promo out of orders) never said it needs the orders migrations that create the `orders_promo` table, so migrations only ran in a working order by luck. Adding an app changed the order and a fresh test database failed ("relation orders_promo does not exist"). It now depends on `orders.0035`; both are applied on every existing database, so nothing changes there.

---

## 2026-10-19: Dish discounts work again, and the Android app 1.2.0

### Fixed
- **A dish's own discount never applied** (the tag button next to a dish on the bill page). The page posted to `/orders/item-discount/<id>/`, but the orders app is mounted at the site root, so it was a 404 since 31 Mar 2026; and because the 404 page isn't JSON, nothing was shown either. It also asked "Apply discount %?" with nothing to type in, then opened two browser prompts. Now it is one form (percent and reason, checked before it is sent) posting to the address from its URL name (`{% url 'item-discount' %}` on the button).
- **The PC print agent's receipt data** was fetched from `/orders/qz-data/<id>/`, the same mistake, on the bill and billing pages, so that print path always fell through. Both now use `{% url 'qz-receipt-data' %}`.
- **A dish with an offer had its price pushed left on the bill page.** The price and the green offer line ("happy 60 −₹108.00") shared one left-aligned column, so a wide offer line moved that dish's price about 58 px left of every other amount. Each dish is now a line (name left, amount right) with small rows under it (modifiers, the dish's own % off, its offer with the amount under the price), and every amount sits in one right-aligned column in the same font as the totals (Space Mono has no rupee sign, so ₹ fell back to another font). A browser test measures where each ₹ amount ends on screen and fails if any is more than 1 px off (58 px on the old page).
- **A failed request could say nothing.** `apiClient` in `base.html` threw without a message when the server answered with an HTML page (a 404 or 500 page, a proxy error) instead of JSON. It now shows "Server Error (404). Please try again."
- **Android app: the pages' `alert()`, `confirm()` and `prompt()` never showed.** A WebView with no `WebChromeClient` skips them (`confirm()` answers cancel, `prompt()` answers nothing), so about 90 dialogs across Rasova's pages silently did nothing in the app. New `JsDialogs.kt` shows them as Android dialogs (a number keyboard for a number). Checked on the emulator against the live demo's bill page.

### Changed
- **Android app 1.2.0** (versionCode 4) is on the site at `rasova.net/android/rasova.apk`, **signed with Rasova's own release key** (RSA 4096, kept outside the repository on the signing PC; `app/build.gradle` signs with it when present). Versions up to 1.1.1 were signed with a development key, so **a phone with the old app must uninstall it once**; Android refuses the new one over it ("App not installed"), which was checked on the emulator. The printer setup page, the "update the app" message and `PRINTING_SYSTEM.md` say so. It also carries 1.1.1's https-only server addresses, which never shipped on their own.
- `menu/templates/menu/menu.html` removed: no view rendered it, and its "call waiter" button posted to an address that doesn't exist.

### Tests
- `core/tests/test_page_addresses.py`: reads every literal address in a `fetch()` or `apiClient` call in the templates and static scripts, fills in its variable parts and resolves it against the URL map. It found the three wrong addresses above (it fails on the old code with exactly those) and stops the next one at CI.
- `orders/tests/test_item_discount_browser.py`: the dish discount in Chromium (Playwright): the form applies 10% (₹300 to ₹270), a missing reason is stopped in the form, a refusal is shown; no browser prompt, no JavaScript error. All three fail on the old page. Skips without Playwright, like the promo browser test.
- Android: unit tests pass on the release build; the release keeps `JSBridge`'s four methods after R8 shrinking.

---

## 2026-10-18: Promos follow the bill

A second review of promos, after the 3 Oct hardening. Every finding was proved by running it against the code first; 31 new tests in `promos/tests/test_promo_rules.py` fail on the old code (27 of them; the other 4 guard what already worked), plus a browser test.

### Fixed
- **Voiding or comping dishes below a promo's minimum left the promo on.** "₹200 off above ₹1,000" on a ₹1,200 bill, with the ₹1,000 dish voided, made the bill ₹0. The minimum is now part of the bill's maths (`tax_engine.compute(discount_min_order=...)`, rule 1), so every path that changes a bill follows it. Below the minimum the promo **pauses**: it stays on the bill, takes nothing off, the bill screen says what it needs, and it applies again by itself once the bill gets back to the minimum. Its use stays with the bill.
- **At an outlet whose prices include GST, a ₹1,000 bill was "below ₹1,000".** The minimum was compared with the value before tax (₹952.38). It is now the dishes' menu value, what the guest reads on the menu, the same in both pricing modes.
- **Sending an order with a 0 discount left the promo on the bill** with its use taken and nothing off. A bill's discount (type, value, promo and its copied name, code and minimum) is now set only by `orders/services/bill_discount.py`, which every path uses (bill screen, create order, the model helpers), and the database refuses a promo with no discount (`order_promo_has_discount`).
- **The audit report counted promo taps,** including promos taken off again and bills later cancelled. It now counts paid and closed bills, with what each promo took off (`Order.order_discount_total`).
- **No bill said which promo it got.** The discount line is now `Promo HH20` (or the promo's name, cut to fit a 58 mm slip) on the bill screen, thermal and A4 bills, the PDF, the WhatsApp bill link and ESC/POS printers. A dish's own discount stays on its own "Discount" row; the rows still add up.
- **The Setup screen said "Expired" for a promo that hadn't started or was used up.** It now says Active, Paused, Starts 25 Oct, Used up or Ended 12 Oct, by the outlet's business day. "Total Promos" shows the count again.
- **An archived ("deleted") promo could be switched back on** through the toggle. It now answers 404.
- **A manager could make a promo for another outlet, or for every outlet.** The owner manages every promo; a manager only their own outlet's. A promo for every outlet is the owner's alone (create, edit, pause, delete all say so); a manager sees it marked "Owner".

### Added
- **Edit a promo** from the Setup screen (pencil button), checked like a new one. Bills already holding it keep the values they got.
- **A flat promo needs a minimum order at least as big as itself,** so "₹100 off" can never make a ₹60 coffee free.
- The Setup screen shows what each promo has given away on paid bills.

### Changed
- **A promo's uses are the bills holding it** (`Promo.uses`: cancelled bills don't count), not a counter every path had to move by hand. Taking a promo off a bill gives its use back with nothing to undo; applying the same promo again takes its current values and no extra use. A cancelled bill keeps the record of its promo. The cap check locks the promo row, so two tills can't both take the last use.
- `promos/services.py` (attach/release) is gone; `bill_discount.py` replaces it. `Promo.validate_and_use()`, `record_use()` and `release_use()` are replaced by `Promo.refusal_for()` and `Promo.locked()`.
- `Order.recalculate_totals()` saves only what the engine worked out; the discount itself is saved by `bill_discount.py`, all its fields together.
- The frozen copy of the tax maths (`orders/tests/legacy_totals.py`) has the minimum rule too, and new random-bill tests check the real engine against it on both sides of the minimum.

### Migrations
- `orders 0068`: `Order.promo_code`, `promo_min_order`, `order_discount_total`; bills holding a promo get its code and minimum; a bill left with a promo and nothing off loses the promo. `orders 0069`: the `order_promo_has_discount` rule (its own migration: Postgres won't alter the table in the transaction that just updated it).
- `promos 0003`: `usage_count` leaves the model but **stays in the database for one release** (with a database default), so workers still on the old code during a deploy, or a rollback, keep working. Drop the column in a later release.

### Tests
- `promos/tests/test_promo_rules.py` (31), `promos/tests/test_promos.py` (moved from `promos/tests.py`, rewritten for counted uses), `orders/tests/test_totals_properties.py` (`PromoMinimumMatchesFrozenCopyTest`).
- **CI now runs the `offers` app's tests.** It was missing from the app list in `.github/workflows/ci.yml`, so the offers engine, offers on bills and the `offers.js` Node cross-check had never run in CI since the app arrived on 4 Oct (they pass). Every app with tests is now in the list.
- `promos/tests/test_promo_browser.py`: the whole story in Chromium with Playwright against a live server (owner creates with the real form, the form refuses a flat promo with no minimum, cashier picks it on the bill, the print view and WhatsApp link name it, a void pauses it, the owner edits it, a manager can't change the owner's promo, no JavaScript errors). Skips when Playwright isn't installed, so CI is unchanged. `PROMO_E2E_SHOTS=<folder>` saves screenshots.

---

## 2026-10-12: Every link and print agent on https

### Fixed
- **Links Django built were http:// behind Cloudflare.** Cloudflare reaches nginx over plain http, so `request.is_secure()` was always False and `build_absolute_uri()` produced `http://` links: the Razorpay and aggregator webhook URLs shown in Setup and the WhatsApp bill links. With Cloudflare's "Always Use HTTPS" now on, `http://` answers with a 301, which webhook senders do not follow. New `core.middleware.CloudflareSchemeMiddleware`, first in the chain, reads Cloudflare's `CF-Visitor` header (believed only from a Cloudflare address, the same rule as `get_client_ip`), so links are `https://`. 11 new tests (`core/tests/test_https_scheme.py`), including one through the whole middleware stack that fails without the middleware.
- **Google Search Console: "Alternative page with proper canonical tag" for `http://rasova.net/`.** The http address served the home page instead of redirecting. Fixed at Cloudflare (Always Use HTTPS).
- **Android print app 1.1.1: internet addresses are always https.** New `ServerUrl` upgrades `http://` for public hosts wherever an address enters the app (setup screen, the page handing over the agent's server, addresses saved by older versions); local and emulator addresses are left for testing. A redirected POST becomes a GET in OkHttp, so an agent on `http://` would never mark jobs printed. 7 new unit tests.

### Changed
- **HSTS now actually goes out**, because Django can see https. It starts at one day (`SECURE_HSTS_SECONDS`, default 86400) instead of a year, so a subdomain not ready for https is not locked out; raise it after a week.
- `core.utils.get_client_ip`: the "who connected to nginx" step is now `_connecting_peer()`, shared with `visitor_used_https()`. Behaviour unchanged (its tests pass as before).

---

## 2026-10-08: Pages for each kind of outlet

### Fixed
- **The outlet pages undersold offline.** They said offline billing isn't built; in fact orders and cash payments made during a short drop are queued on the device and sync when the connection returns (`offlineQueue`, `offlinePaymentQueue`). Each page now says that, and that UPI and card need the internet and long outages aren't covered.

### Docs
- README: pub/bar type, the tax engine and bills, offers, the Android print app, the public site and search rules, 34 feature flags, 2,062 tests and how to run them in parallel, roadmap.
- `docs/TESTING_STRATEGY.md`: offers tests, the Node cross-checks, search and public-page tests, parallel runs, what CI really runs.
- `docs/USER_MANUAL.md`: offers and happy hours, liquor on the bill, token numbers and the Order Ready board.
- `docs/DEPLOY.md`: deploying from a private repository (CI and by hand), public pages.
- `docs/SUBSCRIPTION_TIERS.md`: how the internal tier names map to the Solo and Growth plans on the website.

### Added
- **Four public pages, one per outlet type:** `/fine-dining-pos/`, `/cafe-pos/`, `/qsr-pos/` and `/bar-pos/`, in the same design as `/compare/`. Each claims only what that tenant type really gets (features checked against `core/features.py` and the code): pub mode's liquor outside GST with food on GST on one bill, happy hours by the clock; cafe counter billing, tokens, phone printing, composition Bill of Supply; QSR tokens, Order Ready board, central kitchen, shift cash closing; dine-in floor plan, waiter phones, stations, merge and split. Each has an honest "what to know before you switch" list (internet needed, no offline billing yet) and FAQ structured data generated from the visible questions.
- **Home and compare link to all four** (an outlet-type row on the home page, and a "Rasova for" row in both footers). The home footer now wraps.

### Changed
- **One list of public pages,** `core/public_pages.py`, read by both the sitemap and `SearchIndexingMiddleware`, so a page can't be in the sitemap but marked noindex, or the other way round. Tests: `core/tests/test_public_pages.py` (fails if a page in `public/` isn't listed; the serving tests fail on the old middleware and sitemap).

---

## 2026-10-06: Search indexing

### Fixed
- **Google indexes only the real public pages.** WhiteNoise served the marketing pages on every subdomain, so Google indexed `spice.rasova.net/compare/` instead of `rasova.net/compare/`, and it indexed `rasova.net/login/`. New `core.middleware.SearchIndexingMiddleware` (before WhiteNoise): only `rasova.net/`, `rasova.net/compare/` and a restaurant's guest menu are indexable; every other HTML page gets `X-Robots-Tag: noindex`. `/compare/` on a subdomain and all of `www.rasova.net` move to `rasova.net` with a 301 (GET and HEAD only). New setting `CANONICAL_HOST` (default `rasova.net`). Tests: `core/tests/test_search_indexing.py`.

---

## 2026-10-05: GitHub code scanning alerts

### Security
- **Only messages written for the user reach a screen.** CodeQL flagged 43 views answering with `str(e)` (py/stack-trace-exposure). Most were Rasova's own messages; two were real leaks: receiving a purchase order answered a `TypeError`/`AttributeError` with Python's own words, and the kitchen, order-API and order-source views caught any `ValueError`, so an unexpected one would have been shown too. Now `core/errors.py` holds `UserError` (every Rasova message class is one: number inputs, offers, promos, orders, cart, menu, recipes, units, plus new `QuantityError`, `OrderSourceError`, `KitchenStateError`) and `error_response()`, which shows a `UserError` or a model `ValidationError` as written and logs anything else behind "Something went wrong. Please try again." Tests: `core/tests/test_user_errors.py`.
- **The print agent download** opens a path fixed in code (`AGENT_PATHS`); the URL only picks a name (py/path-injection).
- **No part of an API key is logged or printed** any more (the AI service and `scripts/test_ai.py` showed the last 4 characters).
- **The Offers screen's example bill** is built from elements with every name and number set as text, never HTML (js/xss-through-dom).

## 2026-10-04: edit an offer; offer hours are clock hours

### Fixed
- **An offer starting before 6 AM never showed.** Found live: "happy 60", every day 05:00 to 20:00, missed a Cold Coffee billed at 10:22. The window's times were counted from the 6 AM start of the business day, so 05:00 to 20:00 became 5 to 6 AM only. Times are now plain clock times as an owner reads them; a window ending before it starts (Friday 22:00 to 02:00) still runs past midnight, the part after midnight belonging to the day it started; days without times are still the whole business day. Fixed in `offers/engine.py` and its browser copy `static/js/offers.js` (checked against each other on 600 random carts).

### Added
- **Edit on the Offers screen.** Each offer has an Edit button that loads it back into the form, with the live example bill. Saving checks it exactly like a new offer. Open bills the offer can reach are re-totalled at once, so a mistake (50% for 5%) stops costing money immediately; paid bills keep what they were billed. Every edit is recorded (`offers.OfferChange`: who, when, each field from what to what) and shown on the offer's row. Tests in `setup/tests/test_offer_setup.py`.

## 2026-10-04: the Offers setup screen

### Added
- **Setup, then Offers** (`/setup/offers/`, owner and manager, pubs by default): pick a kind (buy N get free, happy hour, % off, Rs off each), the dishes or whole categories it covers (nothing picked: the whole menu), the days and hours (business-day nights, so 20:00 to 02:00 works), dates and priority. A **live example bill** with the menu's real prices shows what the guest pays and saves before saving, worked out by the same scripts the carts use; change any quantity to try it.
- Every offer listed with what it covers and when; switch one off (dishes already ordered keep it) or remove it (archived: bills that used it keep it); **Pause all** for a bad night.
- Every choice checked with a message that says what to fix (a happy hour needs its hours; buy and free together at most 20; dishes must be on this outlet's menu; an all-outlets offer, owner only, covers the whole menu). A manager sets offers for their own outlet only.
- Tests: `setup/tests/test_offer_setup.py`, including the page's own script run in Node to price the example bill.

## 2026-10-04: offers (buy N get one free, happy hour)

### Added
- **The offer engine** (`offers/engine.py`): offers that apply themselves by rule, so nobody types a discount. Three kinds: buy N get M free (the cheapest M of every group of N + M, dearest first), percent off and rupees off, each for some dishes or categories, on some days and times. On by default for the **pub** tenant type only (feature `offers`); set up in Django admin for now (Offers), the Setup screen comes next.
- **The rules, each with a test** (`offers/tests/`):
  - The price is locked when a dish is ordered: a pitcher ordered at 7:55 is a happy-hour pitcher even if the bill comes at 8:20, and switching an offer off mid-meal keeps the dishes already ordered (`OrderItem.added_at`, `Offer.paused_at`).
  - Days, times and dates follow the business day: a Friday window of 8 PM to 2 AM covers 1 AM on Saturday.
  - Every change re-checks the whole bill: a fourth pitcher waits for its group, a voided pitcher withdraws the free one, a guest's QR order gets the offer from the server, never from the guest's screen.
  - Never on a free dish or one with a staff discount (no stacking), never on liquor in a parcel, never on a Zomato or Swiggy order. Paid modifiers (a mixer) are never discounted; the offer takes off the dish's own price.
  - One offer per line: highest priority first, then the one worth more to the guest, so two cashiers get the same bill whatever order they tap in.
  - An offer comes off the dish's value before tax, like a dish discount, through the tax engine; a bill discount or promo then applies to what is left.
  - An issued bill never changes; each line keeps the offer's name as it was, and an offer used on a bill can only be archived, not deleted.
- **On every bill**: an "Offers" row apart from "Discount", and the offer under each dish it touched, on the bill page, thermal and PDF bills, printed receipts and the WhatsApp bill link. `Order.offer_total` and each line's `offer_discount` keep the figures for reports.
- **In the carts**: the POS, the QSR counter and the QR menu show the offer before the order exists (`static/js/offers.js`, a copy of the engine checked against the Python on 600 random carts and offers, and the cart totals on 500 more).

### Fixed
- **The day-end (Z) report's item list counted voided dishes, and free dishes at full price.** It now counts what each dish was billed at (offers off too), and the totals show "of which Offers".
- **A pub with liquor on the menu couldn't be deleted at all**: a drink's link to its liquor class was PROTECT, which refuses even when the whole restaurant goes in one delete. It is RESTRICT now (a class in use still can't be deleted on its own). `menu.0017`, no database change.

## 2026-10-03: the test suite runs in parallel

### Changed
- `python manage.py test ... --parallel 8` now works on Windows: all 1949 tests in about 25 minutes, where one process took over an hour. Two things stopped it before. `tblib` was missing, so the first failing test's traceback couldn't be sent back from its worker and the whole run crashed (now in `requirements-test.txt`). And the tests wrote to the real `logs/` files, which the workers all tried to rotate at once ("being used by another process"); during tests the file handlers are switched off (`core/settings.py`), the console log and `assertLogs` work as before.

## 2026-10-03: every number staff type is checked

### Fixed
- **Odd numbers were saved, or became a 500.** The screens read numbers with a bare `Decimal()`, which accepts "NaN", "Infinity" and "1e20" and lets negatives through. Run against the real code: a negative outlet parcel charge (-50) and opening cash (-500) were saved; a negative dish price, low-stock level, cost price, tip or shift base pay was saved; NaN in a payment, refund, expense, restock, cost price, pay rate or discount, and Infinity or 1e20 as a dish price, was a 500; negative stock on a new inventory item was a 500 (the database check); NaN as a discount on the order API was dropped without a word, and "Infinity" as a flat discount was a 500. All of these now go through `core.validators.read_number`, which refuses anything that isn't a real number, a negative where it makes no sense, too many decimals, and anything bigger than its database column holds, with a message that says what to fix ("The opening cash can't be negative."). A stock adjustment can still go down; the order API still clamps a negative discount to 0 and a percent to 100, as before.
- **A refund over the amount left said "try again".** The refund view turned every refusal into a 500, so "Refund request exceeds available amount. Max: ..." never reached the manager; a wrong payment id was a 500 too. They are a 400 with the real reason and a 404 now.
- **The setup wizard saved a negative dish price.** Its first-menu step read prices the same way; a bad price now skips that dish with a message saying why, and the wizard carries on.
- A bad outlet parcel charge was dropped with only a log line; the owner now sees "The parcel charge must be a number. It was left as it was." and the rest of the form saves. A bad parcel charge on a dish now refuses that edit with a message instead of saving the rest and dropping it.
- Tests: `core/tests/test_number_inputs.py` (every screen test fails on the old code).

## 2026-10-03: the shifts and reservations pages open on the business day

### Fixed
- Both pages listed the calendar date, so at 1 AM the shifts page was empty while tonight's staff were still clocked in, and the reservations page showed tomorrow. They now use the business day (6 AM to 6 AM by default) like the rest of the app, for the default and for a chosen date; the weekly schedule opens on the business day's week. Tests: `core/tests/test_business_day_pages.py`.

### Docs
- README: the test list said a cashier cannot discount. A cashier or captain can, up to the outlet's staff limit and with a reason, and cannot close a bill without payment.

## 2026-10-03: closing a bill without payment

### Fixed
- **A manager's back arrow on an unpaid bill closed it with no payment.** The link was labelled "Dashboard (Bypass)" and posted to `log-bypass` on the way out, freeing the table and ignoring any error, so going back to the tables lost the bill's money without a word. The back arrow now only goes back. Closing an unpaid bill is its own **Close Without Payment** button (manager or owner): it says how much stays unpaid, asks for a reason, and stays on the bill if the server refuses.
- **The manager limit of 3 a day reset at midnight.** It counted from IST midnight, so a manager could close 3 bills before midnight and 3 more after, in one business night. It now counts the business day (6 AM to 6 AM by default), and two quick taps can't both slip under it (the manager's row is locked while counting).
- **No reason, and no report.** A reason is required now, and each bill closed without payment is on the Discount / Void Audit report and its CSV: when, bill number, amount unpaid, who, why. Ones closed before today show their unpaid amount with "(no reason recorded)".
- A cancelled bill could be "closed" this way; it is refused now.
- The bill page's own promo list now uses the same rule as the rest of the promo fix (`is_live_for`, the business day).
- Tests: `orders/tests/test_close_without_payment.py`.

## 2026-10-03: a sold dish can't be deleted

### Fixed
- **Deleting a dish deleted it from every bill that sold it.** `OrderItem.menu_item` was `on_delete=CASCADE`: run against the real code, a paid bill went from one line to none while its total and bill number stayed, so the bill, the item and category reports and the GSTR-1 HSN table lost the sale. Deleting the dish's category did the same. The line is now `RESTRICT` (`orders.0066`, no database change), and the menu screens refuse first with a message: a sold dish should be switched off, which hides it and keeps it on old bills; a category says which of its dishes were sold. A dish or category never sold can still be deleted, and deleting a whole restaurant (or the demo reset) still works, because the bills go in the same delete. Tests: `orders/tests/test_keep_sold_dishes.py`.

## 2026-10-03: promos and discounts hardened

A review of the promos, with each suspicion run against the real code first (`md_files/rasova_pub_offers_design_and_promo_review_2026-10-03.html`), found six problems and two more discount paths. All fixed, each with a test that fails on the old code (`orders/tests/test_discount_hardening.py`, 30 tests; four deliberate breaks of the fixes were each caught).

### Fixed
- **A promo's uses were counted per tap and never given back.** Applying the same promo three times on one bill used three of its uses; removing or replacing it, or cancelling the bill, gave none back. The order now remembers its promo (`Order.promo`, `Order.promo_name`, `orders.0065`), applying it again is free, and every way a promo stops applying returns its use (`promos/services.py`). A paid bill keeps it.
- **A bill didn't say which promo it used**, and deleting a promo left no trace. The order and the audit event now name the promo; deleting is archiving (`Promo.archived_at`, `promos.0002`), and an archived promo frees its code.
- **Promo dates followed the calendar, not the business day**, so a promo valid until Saturday stopped at midnight in the middle of Saturday night. Validity now uses the outlet's business day (until the day's cutoff, 6 am by default).
- **A second, older copy of the promo endpoints** (`promos/views.py` create, toggle, delete) ignored the minimum order, the usage cap and the all-outlets choice. Removed; the Setup screen's endpoints are the only ones, and are now tested.
- **The promo form saved mistakes**: an end date before the start, a usage cap of 0 (which means unlimited), a negative minimum; a bad date or a text cap was a 500. Each is refused with a message saying what to fix; a used code says so.
- **Manual discounts had no reason and no ceiling.** A typed bill discount, a dish discount and a free dish now need a short reason, which goes on the audit event. Cashiers and captains may give up to the outlet's new **staff discount limit** (Outlet Settings; a free dish counts as 100%); above it a manager or the owner applies it. With no limit set nothing changes, as agreed for the captain role; the pub preset sets 10%. Promos need neither.
- **Two discount paths no screen uses** (a bill discount and a dish discount sent with the order itself) had no limit, no reason and no audit event. They follow the same rules now, and a refusal rolls the whole request back.

### Added
- **The Discount / Void audit report** shows the reasons given for discounts and free dishes and which promos were used, and no longer counts taking a discount off as a discount.

## 2026-10-03: the QR menu's cart opens again

### Added
- **A "Pub / Bar" tenant type** (`tenants.0039`, a choices-only migration). A pub gets the fine-dining set by default (floor plan, kitchen, QR menu, split bills, reservations, inventory, reports) plus `liquor_vat`, which stays off for every other type, and never the composition scheme (a liquor seller can't use it, s.10(2)(b)). Staff of a pub land on the floor plan and billing screen like fine dining: the login redirect asked "is this fine dining?" in three places, and now asks `Tenant.is_table_service` (fine dining and pub). The superuser panel offers it when creating a restaurant, and there is a "pub" preset. A pub created with no overrides bills drinks under VAT; the same cart at a fine-dining tenant stays GST (tested). Buy-one-get-one and other offers will be added to this type only, when built.

### Fixed
- **The superuser "create restaurant" form stored any text as the restaurant type** (a typo became an unknown type that quietly behaved as fine dining). It now refuses a type that isn't one of the choices.

### Fixed
- **VIEW CART did nothing on the QR and digital menus** (since the 28 Sep deploy). The liquor work (`af6b70d`) made the cart read each dish's tax from a `DISH_TAX` table, and the billing and token screens got the line that reads it from the page, but `menu/digital_menu.html` didn't: opening the cart threw `ReferenceError: DISH_TAX is not defined` before the sheet appeared, so guests could add dishes but never see the cart or place the order. The page now reads the table like the other two carts.

- **Deploys failed once the repository was private.** The deploy step put the stored token in the server's git URL as a username with no password, which GitHub only skipped checking while the repository was public, so the 3 Oct deploy stopped at `git fetch` (nothing on the server changed). The server now fetches with the run's own read-only token as the password; it is revoked when the job ends, so no long-lived token is left in the server's `.git/config`.

### Added
- **A test that runs the QR cart** (`menu/tests/test_qr_cart_js.py`) - the rendered page's scripts run in Node against a small stand-in for the browser, and a guest taps ADD and VIEW CART through the page's own buttons; it checks the cart opens with its dishes and the right GST and VAT. The existing cart tests only read the HTML, which is how this got through. It fails on the old page with the same ReferenceError.

## 2026-10-02: cleanup, one superuser panel

### Removed
- **The `/portal/` panel** (`portal/` app) - a second copy of the superuser panel at `/superuser/`. Both had been kept up in parallel, and `/superuser/` is the complete one: it also has subscription billing (plans, invoices, marking paid), which `/portal/` never got. `core/urls.py` tried to redirect `/superuser/` to `/portal/`, but `accounts/urls.py` registers `/superuser/` first, so those redirects never ran; they are gone. `robots.txt` now keeps crawlers off `/superuser/`. No database tables (the app had none).

- **Dead templates** - `orders/qsr_bill.html` (no view rendered it), `core/landing.html` (an old copy of the marketing page; `/` is `public/index.html`), `inventory/purchase_order.html` (its view only redirects now), `orders/templates/errors/404.html` and `500.html` (the handlers render `templates/404.html` and `500.html`).
- **`robots.txt` at the top of the repository** - never served (`/robots.txt` is the view in `core/urls.py`; WhiteNoise serves files only from `public/`), and out of date.
- **Duplicate scripts** - `scripts/seed_restaurant.py` (a byte-for-byte copy of `orders/scripts/seed_restaurant.py`, which the `seed_restaurant` command uses) and `orders/scripts/simulate_restaurant_rush.py` (nothing imported it; the command has its own code).

- **64 unused imports** in 48 files, found by a scan and checked one by one: none is imported from those modules elsewhere or patched by a test. Two stay on purpose: `crm/models.py` imports `GuestFeedback` so Django registers the model (now marked so), and `core/gemma_service.py` is kept untouched as the start of a local-model fallback for the AI import.

### Added
- **A lint step in CI** (`ruff check .`, `ruff.toml`) - unused or undefined names, unused variables, redefinitions and syntax errors now fail the build, so dead code can't pile up again; style isn't enforced. Getting to zero removed what was left: unused variables in views (`create_order` read the discount fields twice, the thermal receipt worked out a character width it never used, the floor plan built a lookup it never used, the category report an expression it never used), `except ... as e` with no `e`, f-strings with nothing in them, and test variables that were created and never read (the objects are still created). `core/gemma_service.py`, parked for a planned local-model fallback, is exempt.

### Moved
- **Documents into `docs/`** - `DEPLOY.md`, `ELI5_PRINTING_ARCHITECTURE.md`, `MEDIA_AND_BACKUPS.md`, `TESTING_STRATEGY.md`, `USER_MANUAL.md`, `parcel_charge_explainer.md`, and the tracked schema diagrams into `docs/diagrams/`. `README.md`, `CHANGELOG.md` and `PRINTING_SYSTEM.md` stay at the top. Links in the README, `deploy.sh` and the backup scripts follow.
- **Developer tools into a `devtools` app** - the 13 load-test, seeding and print-preview commands (`load_test`, `pub_night_test`, `http_rush_test`, `seed_restaurant`, ...) and the scripts they wrap move out of `orders`; they run by the same names. `reset_demo_tenant` stays in `orders` (the live demo uses it), and the live demo's seed moves from `orders/scripts/demo_seed.py` to `orders/services/demo_seed.py`, since it is product code. `orders/scripts/` is gone.
- **Printing code into the printing app** - the ESC/POS service (`orders/services/printing_service.py` to `printing/services/printing_service.py`) and the two print tasks (`orders/tasks.py` to `printing/tasks.py`). The tasks keep their registered names (`orders.tasks.print_kot_task`, `orders.tasks.print_bill_task`), so jobs already queued still run and the queue routes don't change; `orders/tasks.py` keeps the demo reset task. The print views stay with the order pages they serve (`/print-bill/<order>/` and the rest).
- **One test layout** - `core`, `accounts`, `setup`, `menu` and `inventory` mixed a `tests.py` with `test_*.py` files beside the code; each now has a `tests/` package (the old `tests.py` is `tests/test_<app>.py`), as `orders`, `reports` and `tenants` already did. Apps with a single `tests.py` keep it. The tenant-scoping test helpers (`as_tenant`, `TenantScopedTestCase`, formerly `core/test_utils.py`, which looked like a test file) join `freeze_ratelimit_clock` in `core/testing.py`.
- **`.gitattributes`** - `.bat` files check out with Windows line endings (cmd.exe can misread an LF-only batch file, and the agent installer is downloaded and run as is), `.sh` files with Unix ones.

### Fixed
- **A flaky webhook test** - `ReplayTest` in `orders/tests/test_aggregator_ingest.py` sent a request stamped 301 seconds ahead against the 300-second window, but in whole seconds, so it had under a second of margin: on a slow run the server saw it as 300.x seconds ahead and took it (the 2 Oct full run failed this way). The tests now freeze the webhook's clock, and a new test pins the exact edge (299.9 s in, 300.1 s out). The webhook code was right and is unchanged.
- **An agent test that only passed on Windows** - `test_watchdog_bat_launches_pythonw_when_available` checked only that the watchdog script named some `.exe`, which is true on Windows and false on the Linux CI runner (so the 2 Oct push did not deploy). It now builds a Windows-style Python folder and checks the watchdog runs `pythonw.exe` when it's there and `python.exe` when it isn't, on any OS; breaking the pythonw preference fails it.

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
