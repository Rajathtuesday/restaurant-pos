# Load Testing

Two Django management commands, built to answer two different questions:

| Command | Question it answers | Layer tested |
|---|---|---|
| `python manage.py load_test` | Is the code *correct* under concurrency, and does it *stay correct* over a sustained period? | Direct DB / service layer |
| `python manage.py http_rush_test` | Does the *server* hold up under real concurrent HTTP traffic? | Full HTTP / WSGI / middleware stack |

Both create clearly-marked throwaway tenants (`loadtest-tenant`, `loadtest-rush-N`), delete them when finished (unless you pass `--keep`), and never touch real restaurant data.

Both write a full, human-readable, timestamped report to `loadtest_logs/` (gitignored) in addition to printing to the terminal — so a run can be reread later without having watched it live.

---

## `load_test` — DB-level correctness (4 phases)

```bash
python manage.py load_test                                    # all defaults
python manage.py load_test --orders 300 --workers 16           # bigger throughput run
python manage.py load_test --keep                              # skip cleanup, inspect the data after
python manage.py load_test --no-race --no-reservation-race     # skip straight to soak, see below
python manage.py load_test --soak-minutes 45 --soak-workers 8  # + the sustained phase
```

Run this against Postgres. On SQLite the single-writer lock serializes everything and the numbers are meaningless (the command warns you if it detects SQLite).

### Phase 1 — Throughput

Runs the real order pipeline (`get_or_create_open_order` → `add_items_to_order` → `create_kot` → `process_payment`) under `--workers` concurrent threads, each on its own dedicated table (so there's no artificial single-table contention — the real stress here is the *shared* per-outlet daily counters that every worker increments under `select_for_update`).

**How to read it:**
- `completed / failed` — should be 100% completed at reasonable scale. Any failures print an error-class breakdown underneath.
- `throughput` — orders/sec sustained across the whole phase.
- `latency` — p50/p95/p99/max/mean. p50 tells you the typical case; p95/p99 tell you what your worst-off customer experiences. A p50 that stays low while p95/p99 balloon usually means occasional lock contention, not a systemic slowdown.
- `resources` — CPU%/memory of *this process* during the phase (see the resource-sampling caveat below).
- `db connections` / `waiting locks` — a Postgres snapshot before/after. Waiting locks > 0 after the phase is a real signal worth investigating; some waiting *during* a phase is normal (that's what `select_for_update` is for).

### Phase 2 — Table-booking race probe

`--race-workers` threads all try to open an order on **one single table** at the exact same instant. This exists to prove the unique-open-order-per-table guard holds under real concurrency, not just in theory.

**How to read it:** it should always say `PASS` — exactly 1 distinct order ID, exactly 1 open row in the DB. A `FAIL` here means two customers scanning the same table's QR code at nearly the same moment could end up on two different bills — a real, serious bug, not a cosmetic one.

### Phase 3 — Reservation race probe

Same idea, aimed at `POST /crm/reservations/create/`: `--race-workers` threads all try to book the **same table at the same time slot** simultaneously.

**How to read it:** `PASS` means exactly 1 booking succeeded (200) and the rest were correctly rejected (409, "already booked"). This is calling the real view function directly (via `RequestFactory`, not over HTTP) — same DB-level approach as Phase 2, so it works without a running server.

### Phase 4 — Soak test (opt-in, `--soak-minutes N`)

Off by default — this is the only phase that takes real wall-clock time. Holds `--soak-workers` threads running the same order lifecycle continuously for `N` minutes, logging a checkpoint every `--soak-checkpoint-seconds` (default 60): cumulative completed/failed count, memory/CPU since the last checkpoint, and current DB connection/lock state.

This is deliberately **not** part of `http_rush_test` — `/create-order/` rate-limits at 20 requests/min per IP, which would mean an HTTP-level soak test spends 29 of every 30 minutes measuring the rate limiter, not the app. The soak phase bypasses HTTP entirely for exactly this reason.

**How to read it:**
- Each checkpoint line — watch `completed` climb roughly linearly. A throughput that visibly slows down over time (fewer completions per checkpoint, not the same rate) is itself a finding, even before looking at memory.
- **The memory verdict at the end is the real point of this phase.** It compares the mean memory of the first half of checkpoints against the second half:
  - `Memory looked stable: X -> Y (+Z%)` — no leak signal.
  - `WATCH: mean memory grew Z% ... and was still climbing at the end` — worth a longer soak to confirm. A leak shows up as memory that keeps climbing and never plateaus; a one-time warm-up bump (caches filling, connection pools opening) shows up as growth that *stops* climbing partway through — the tool only flags the former.
- Ignore isolated CPU% spikes at the very start of a checkpoint (occasionally shows a large, clearly-wrong number like 1800%) — that's `psutil` needing a moment to stabilize after each checkpoint's sampler restarts, not a real event. The memory numbers are the trustworthy signal in this phase; CPU is secondary.

---

## `http_rush_test` — real HTTP capacity

```bash
python manage.py http_rush_test --host http://127.0.0.1:8000
python manage.py http_rush_test --host http://127.0.0.1:8000 --tenants 8 --requests-per-tenant 15 --workers 20
python manage.py http_rush_test --host https://rasova.net --allow-remote --tenants 3 --requests-per-tenant 5
```

Seeds `--tenants` throwaway restaurants, each with its own outlet + counter QR token, then fires real HTTP `POST /create-order/` requests (the same request path a real QR-scanning guest's browser sends — first loading the menu page to establish a CSRF cookie, exactly like a real browser would, then posting a cart) concurrently across all of them at once against `--host`.

**Safety guardrail:** refuses to run against anything that isn't `localhost`/`127.0.0.1`/a private IP unless you also pass `--allow-remote`. This exists specifically so the command can never accidentally fire concurrent load at production by a typo or a copy-pasted command — it's an enforced check, not just a warning in this doc.

**The one thing you must know before trusting any number this prints:** it only tells you about the capacity of whatever machine the *server* is running on.
- Point `--host` at a server running on the same machine you're running this command from, and the numbers reflect *that machine* — running it against your own laptop's dev server tells you about your laptop, not your EC2 box.
- For real production capacity numbers, run this command *from a separate machine* than the server (so the load generator isn't competing with the app for the exact CPU/memory you're trying to measure), pointed at the real host with `--allow-remote`.

**The rate-limit gotcha:** `/create-order/` is rate-limited to 20 requests/min per source IP (defense against cart-spam). Every simulated "guest" this tool fires really comes from one machine's IP, so any run whose total request count (`--tenants` × `--requests-per-tenant`) climbs much past ~20 fired in a tight burst will start seeing `HTTP_429` in the error breakdown. **That's the rate limiter correctly doing its job, not a capacity failure** — the command prints this reminder every run so it's not misread as a scary error spike. Keep totals under ~20 if you want clean success-path latency numbers; go over it deliberately if you specifically want to confirm the rate limiter itself is working.

**How to read the output:** same shape as `load_test`'s Phase 1 (completed/failed, throughput, latency percentiles, resource line, DB connections), plus a one-line verdict at the end comparing p95 and error rate against a rough "comfortable" ceiling (p95 < ~1-2s, errors < ~1%).

---

## `scripts/run_load_tests.sh` — convenience wrapper

```bash
bash scripts/run_load_tests.sh                                  # load_test only (DB-level, safe locally)
bash scripts/run_load_tests.sh --host http://127.0.0.1:8000     # + http_rush_test against a running server
bash scripts/run_load_tests.sh --host https://rasova.net --allow-remote
```

Env vars for sizing (all optional): `ORDERS`, `WORKERS`, `RACE_WORKERS` (load_test), `RUSH_TENANTS`, `RUSH_PER_TENANT`, `RUSH_WORKERS` (http_rush_test), `KEEP=1` (skip cleanup on both).

---

## Running this against the real server

There is no staging environment for this project (single EC2 host, single environment) — worth knowing before running anything against it.

**`load_test` needs to run *on* the server itself.** It talks to Postgres directly through Django's ORM, which needs production DB credentials — those only exist on the server, and shouldn't be exposed to a laptop just for this. SSH in, `git pull`, `pip install -r requirements.txt` (picks up `psutil`, the one new dependency this tooling added), then run it — starting smaller than the defaults, and during a quiet hour, since it's genuine concurrent load sharing the same box real customers are on (mitigated, not eliminated, by the throwaway-tenant-and-cleanup design).

**`http_rush_test` is better run *from your laptop*, not the server**, pointed at the real public URL with `--allow-remote` — see the capacity caveat above for why.

---

## Where the logs go

Every run writes to `loadtest_logs/<command>_<timestamp>.log` relative to wherever you ran it from — gitignored, so it won't show up in `git status`, but it's sitting on disk. The file has the exact same content that printed to your terminal, with ANSI color codes stripped so it's clean to read in a plain text editor. If you ran something on the server, `scp` that file back if you want to review it later.
