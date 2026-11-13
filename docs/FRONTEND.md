# The frontend: how a page's code is organised

From 12 November 2026 (phase 0 of the frontend plan). Read this before
adding a page or touching the billing screen.

## The rules, and the test that enforces each

| Rule | Enforced by |
|---|---|
| A template holds markup. Its JavaScript is a module in `static/js/`, its CSS a file in `static/css/`. | `core/tests/test_frontend_hygiene.py`: a baseline per template that can only shrink. |
| No `onclick=`. A button carries `data-action="cart.inc"`; the page's module handles it with one listener (`delegate()` in `core/dom.js`). | the same test |
| Server data reaches a script through one JSON block (`{{ data|json_script:"id" }}`), read with `pageData()` from `core/page_data.js`. Never a template tag inside a script. | the hygiene test counts inline script lines, so there is nowhere to put one |
| Nothing is loaded from a third-party host. Fonts, Bootstrap, icons, SweetAlert2 and Chart.js are in `static/vendor/` with their versions and hashes in `VENDOR.md`. | `test_frontend_hygiene` and `test_static_modules` |
| A page never reads a shared object (`apiClient`, `ui`, `offlineQueue`, `rasovaPlatform`, ...) while it is still parsing. They come from `core/app.js`, a module, which runs after the page is parsed. | the hygiene test's `globals` count |
| Modules import each other by static relative path only (`import { x } from "./y.js"`), no `import()` with a computed string, no `import.meta`. | `test_static_modules` checks every import after `collectstatic` |

## Where things are

```
static/js/core/      what base.html runs on every page
  app.js             builds everything below and wires the header; also the bridge (see below)
  http.js            createClient: JSON in and out, CSRF, the loading bar, errors as toasts, the manager-PIN retry
  offline.js         the order queue and the cash-payment queue (IndexedDB), replayed when the connection is back
  toast.js           createUi: toast, confirm, showLoading, hideLoading (wraps SweetAlert2)
  theme.js           dark and light
  notifications.js   the 8-second poll and the header badges
  platform.js        where Rasova runs; the Android print bridge
  pwa.js             service worker registration, the install banner, the printing sheet
  checklist.js       the setup checklist in the corner
  csrf.js, page_data.js, dom.js
static/js/tests/     node tests for the above (node --test "static/js/tests/*.test.mjs")
static/js/billing/   the billing screen: one module for the dine-in screen, the QSR counter and the token counter
  index.js           reads page-data, handles every data-action, wires the pieces below
  cart.js, parcel.js, dispatch.js, counter.js, running_order.js, menu.js, printing.js, tokens.js, ui.js, urls.js
static/js/bill/      the bill page: payment, discounts, printing, the Razorpay QR (phase 2's first page)
static/js/tables/    the floor plan: floor.js (the cards and sheets as markup), actions.js (what a tap asks the server), loader.js (the 5-second refresh), index.js
static/js/kitchen/   the kitchen display: board.js (the tickets as markup), loader.js (the 5-second refresh, by station), actions.js (start, ready, bump, clear all), light.js (its own light switch), index.js; boot.js is a plain script in the head that sets light or dark before the first paint
static/js/<page>/    phase 2: one folder per remaining page, as above
static/css/rasova.css  the page frame (header, banners, loader, badges, checklist, install prompt)
static/css/billing.css, token_billing.css  the two billing pages; billing_parts.css is what their script draws
static/css/tables.css  the floor plan
static/css/kitchen.css the kitchen display
static/css/bill.css    the bill page; static/css/print/paper_58.css and paper_80.css set the receipt roll's width
static/css/public.css  pages with no app chrome (errors, suspended, login as it is migrated)
static/css/themes/     the theme tokens (--panel-bg, --text-main, --accent-gold, ...)
static/vendor/         third-party files Rasova serves itself; VENDOR.md lists versions and SHA-256
templates/core/base.html         the logged-in app shell
templates/core/public_base.html  no user, no chrome
```

## How a page gets its code

1. The view passes what the script needs as a dict: `{"mode": "token", "outlet_id": 3, ...}`. Addresses go in it
   too, reversed on the server with 0 for an id the script fills in (`orders.views.billing_core.billing_page_data`):
   a script never types an address by hand.
2. The template renders it once: `{{ page_data|json_script:"page-data" }}`.
3. The template loads its module in `{% block extra_js %}`:
   `<script type="module" src="{% static 'js/tables/index.js' %}"></script>`.
4. The module reads the data (`pageData("page-data")`), finds its root element, and
   handles clicks by `data-action`.

Modules run after the document is parsed and before `DOMContentLoaded`
handlers, so a module can touch the DOM at its top level.

## The bridge, and when it goes

`core/app.js` ends by publishing the shared objects on `window`
(`window.ui`, `window.apiClient`, `window.themeManager`, `window.offlineQueue`,
`window.offlinePaymentQueue`, `window.rasovaPlatform`, `window.updateOfflineBanner`,
`window.refreshNotifications`). The pages that still have inline scripts use
them. A page module imports from `core/` instead. When the hygiene baseline is
empty, the bridge is deleted.

## Testing

- `python manage.py test core.tests.test_frontend_js core.tests.test_frontend_hygiene core.tests.test_static_modules`
- `node --test "static/js/tests/*.test.mjs"` runs the module tests on their own.
- `python manage.py test orders.tests.test_billing_browser` runs the billing screens in Chromium (Playwright)
  against a live test server. `core/tests/browser.py` has the base class: `self.run_page(url, steps, user=...)`
  logs in, opens the page, runs each step's JavaScript in it and returns the results with every error the
  browser saw. Use it for a page whose module was just written: it is the only test that fails on a wrong id,
  a button without its `data-action`, or a module that throws while loading. Skipped without Playwright.
- `python manage.py test core.tests.test_browser_smoke_walk` opens every page the owner can reach in Chromium
  (about 80 seconds) and fails on an uncaught error, a console error, a static file that did not load, or
  stylesheet text on the page. Run it with any change to base.html, a shared module or a standalone template.
- A module takes its dependencies as arguments (`createClient({ fetchFn, ui, csrf })`)
  so a test hands it fakes; `app.js` builds the real ones. Keep that shape.
- After migrating a page, run `python -m core.tests.test_frontend_hygiene --write`
  (with `DJANGO_SETTINGS_MODULE=core.settings`) to lower its baseline entry, and
  look at the diff: only the migrated page's numbers should move.

## Updating a vendored library

Download the release's files to the same paths under `static/vendor/`, update
`VENDOR.md` (version, source, `sha256sum`), bump the version in `CHANGELOG.md`,
run the three tests above, and open the billing page once.
