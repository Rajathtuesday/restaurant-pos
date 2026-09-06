# Changelog

All notable changes to Rasova are recorded here, newest first. This file starts
2026-09-05 — it is not a retroactive rewrite of the full project history.
For anything earlier than the "Recent history" section below, `git log` is
the source of truth.

---

## 2026-09-05

### Fixed
- **Split-bill QR accuracy** - the UPI "scan & pay" QR on the bill screen was drawn once at page load from the order's full remaining balance and never redrawn, so splitting a bill still asked the customer to pay everything, and the same stale QR printed onto the receipt. `renderUpiQR()` is now a real function, wired into both the payment-amount field and Split, so the QR always reflects the actual amount being collected.
- **Razorpay QR ignored splits entirely** - a second, worse version of the same problem: the Razorpay QR button never read the split amount at all and always requested the order's full balance from Razorpay, with no way to ask for less. It now accepts and validates a requested amount (bounded to the actual remaining balance) end to end - JS, view, and gateway call.
- **docker-compose.yml environment variable mismatch** - the `db` and `web` services read the database password from two different, cross-wired environment variable names, so a fresh container deploy would have connected with the wrong password. Both now read `DB_PASSWORD`, matching what the app itself expects everywhere else.

### Added
- **Order status for QR-ordering guests** - the order-status banner, which used to sit full-width under the header and push the whole menu down for as long as an order was active, is now a small floating pill (same idea as the existing cart bar) that opens a slide-up sheet with a Received → Preparing → Ready → Served timeline.
- **`celery_worker` and `celery_beat` services in docker-compose.yml** - the app has real scheduled tasks (`CELERY_BEAT_SCHEDULE` in `core/settings.py`) that had no way to run at all in a Dockerized deploy before this.
- 19 new tests covering the split-bill QR fix (`orders/tests/test_bill_qr_amount_sync.py`, `payments/tests.py`) and 5 covering the order-status UI (`menu/tests.py`).

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
