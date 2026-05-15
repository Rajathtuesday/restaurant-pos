# Rasova Printing Architecture
## Complete Guide — Multi-Tenant, Multi-Kitchen, Cloud + Local
**Every edge case. Every failure mode. Monday-ready.**

---

## Part 1 — The Core Problem

Your server is in **Hyderabad**. The printer is in **Bengaluru**.

```
  HYDERABAD SERVER                      BENGALURU RESTAURANT
  ┌─────────────────┐                   ┌─────────────────────────────┐
  │                 │                   │                             │
  │  Django         │    INTERNET       │  BillTouch printer          │
  │  Celery worker  │──────────────────►│  IP: 192.168.1.100  ← FAIL  │
  │                 │    TCP :9100      │                             │
  └─────────────────┘                   └─────────────────────────────┘
```

**192.168.x.x is a private address.** It lives inside the restaurant router.
The internet does not know it exists. The Hyderabad server's TCP packet
reaches the ISP and gets dropped immediately. It never reaches Bengaluru.

**The rule: whatever opens the TCP socket to the printer must be on the same LAN.**

---

## Part 2 — The Solution Architecture

```
  HYDERABAD SERVER                      BENGALURU RESTAURANT
  ┌─────────────────────────┐           ┌────────────────────────────────────┐
  │                         │           │                                    │
  │  Django (Gunicorn)      │           │  ┌──────────────────────────────┐  │
  │   handles HTTP          │           │  │  Local Device                │  │
  │   saves orders to DB    │           │  │  (Raspberry Pi / laptop)     │  │
  │   puts print tasks      │           │  │                              │  │
  │   into Redis            │           │  │  Celery Worker               │  │
  │                         │           │  │  RASOVA_TENANT_ID=1          │  │
  │  Redis (task queue)     │◄──Redis──►│  │  RASOVA_OUTLET_ID=1          │  │
  │   listens on :6379      │  TCP      │  │                              │  │
  │   publicly reachable    │           │  └──────────────┬───────────────┘  │
  │                         │           │                 │                  │
  │  PostgreSQL             │           │                 │  LOCAL LAN       │
  │   stores all data       │           │                 ▼                  │
  │                         │           │  BillTouch Printer                 │
  └─────────────────────────┘           │  192.168.1.100 ← reachable!        │
                                        └────────────────────────────────────┘
```

**The flow:**
```
1. Cashier pays → Django saves payment → puts task in Redis
   (Redis is publicly reachable — it has a real IP, not 192.168.x.x)

2. Local Celery worker in restaurant reads from Redis over the internet
   (Redis protocol, TCP, normal internet traffic)

3. Worker checks: is this MY tenant? MY outlet?
   No  → silently skip (another worker will handle it)
   Yes → connect to 192.168.1.100 (same LAN, it works)

4. Bytes sent to printer → paper comes out
   Worker stores idempotency key in Redis → task won't print twice
```

---

## Part 3 — Tenant + Outlet Isolation

### Why both tenant AND outlet?

```
REDIS QUEUE (shared by all restaurants)
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  Job A: tenant_id=1, outlet_id=1, order_id=42  (Spice Garden BLR)  │
│  Job B: tenant_id=2, outlet_id=3, order_id=99  (Pizza Palace PNE)  │
│  Job C: tenant_id=1, outlet_id=2, order_id=55  (Spice Garden DEL)  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
         ▲                    ▲                    ▲
         │                   │                    │
  Worker 1             Worker 2              Worker 3
  tenant=1             tenant=2              tenant=1
  outlet=1             outlet=3              outlet=2
  (BLR branch)         (Pune branch)         (Delhi branch)

  takes Job A          takes Job B           takes Job C
  skips B, C           skips A, C            skips A, B
```

**Why outlet_id alone is usually enough:**
Outlet IDs are global primary keys — no two outlets in the DB share the same ID.
Restaurant A outlet 1 and Restaurant B outlet 1 cannot coexist.

**Why ALSO check tenant_id:**
Defence-in-depth. If a bug somehow created outlets with wrong tenant associations,
the tenant guard catches it. Also makes logs clearer for debugging.

**Code (tasks.py):**
```python
_LOCAL_TENANT_ID = int(os.getenv("RASOVA_TENANT_ID", "0"))
_LOCAL_OUTLET_ID = int(os.getenv("RASOVA_OUTLET_ID", "0"))

# In every print task:
if _LOCAL_TENANT_ID and order.tenant_id != _LOCAL_TENANT_ID:
    return False   # not our tenant, skip
if _LOCAL_OUTLET_ID and order.outlet_id != _LOCAL_OUTLET_ID:
    return False   # not our outlet, skip
```

**Start command for Restaurant A (BLR):**
```bash
set RASOVA_TENANT_ID=1
set RASOVA_OUTLET_ID=1
set REDIS_URL=redis://:password@your-server.com:6379/0
celery -A core worker --loglevel=info -Q printing,default
```

**Start command for Restaurant B (PNE):**
```bash
set RASOVA_TENANT_ID=2
set RASOVA_OUTLET_ID=3
set REDIS_URL=redis://:password@your-server.com:6379/0
celery -A core worker --loglevel=info -Q printing,default
```

Same Redis server. Same queue. Zero interference.

---

## Part 4 — Multi-Kitchen (multiple printers, one restaurant)

```
RESTAURANT — Fine Dining, 3 Kitchen Stations
┌──────────────────────────────────────────────────────────────────────┐
│                                                                      │
│  LOCAL CELERY WORKER (runs on any always-on device in restaurant)    │
│                                                                      │
│  Task arrives: print KOT #7, station_id=2                           │
│  Looks up station in DB → station.printer_ip = 192.168.1.102        │
│  Opens TCP to 192.168.1.102:9100 → prints "2x Chicken Steak"        │
│                                                                      │
│  Task arrives: print KOT #8, station_id=3                           │
│  Looks up → station.printer_ip = 192.168.1.103                      │
│  Opens TCP to 192.168.1.103:9100 → prints "1x Salad"                │
│                                                                      │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ LOCAL NETWORK (192.168.1.x)
         ┌───────────────┼──────────────────────┐
         ▼               ▼                      ▼
  ┌─────────────┐  ┌─────────────┐  ┌────────────────┐
  │ Grill       │  │ Fryer       │  │ Cold / Pastry  │
  │ 192.168.1.101│  │ 192.168.1.102│  │ 192.168.1.103  │
  └─────────────┘  └─────────────┘  └────────────────┘
```

One worker. One machine. All printers reachable because all are on the same LAN.
The worker picks the right printer by reading `station.printer_ip` from the DB.

**Two restaurants, same IP addresses — no conflict:**
```
Restaurant A (BLR)           Restaurant B (PNE)
192.168.1.101 = Grill        192.168.1.101 = Grill
192.168.1.102 = Fryer        192.168.1.102 = Fryer

Worker A is INSIDE BLR LAN.  Worker B is INSIDE PNE LAN.
Worker A sees BLR's 192...   Worker B sees PNE's 192...
Completely separate routers.  No interference.
```

---

## Part 5 — All Edge Cases

### 5.1 Printer is offline / unreachable
```
Symptom: cashier pays, no printout, red "Printer Failure" banner appears
Cause:   printer powered off, wrong IP, WiFi disconnection

What happens:
  print_bill_task → Network("192.168.1.100") → ConnectionRefusedError
  → retry after 5s
  → retry after 5s again
  → MaxRetriesExceeded → _store_printer_error() → Redis cache key set
  → next browser poll of /printer-status/ → red banner shows

Order is SAFE. Payment is SAVED. Only the paper didn't come out.

Fix: power printer on → click "Reprint" on the bill screen.
```

### 5.2 Paper out / paper jam
```
Symptom: printer online, connects, no printout

What happens from software perspective:
  Network("192.168.1.100") → SUCCEEDS (printer is reachable)
  p.text("...") → bytes sent
  p.cut() → bytes sent
  print_kot() returns True ← software thinks it worked
  Idempotency key written → won't retry

The software CANNOT detect paper out. The printer accepts bytes but doesn't print.
Fix: reload paper → manually reprint from /bill/<order_id>/ → Print Bill button.
```

### 5.3 Wrong IP entered in Setup
```
Symptom: Connection refused immediately

What happens:
  Network("192.168.1.200") where nothing exists at .200
  → ConnectionRefusedError instantly
  → retry → retry → MaxRetries
  → printer error banner

Fix: /superuser/tenant/<id>/ → enter correct IP → save.
The easiest way to find correct IP: hold FEED + power on printer → self-test prints IP.
```

### 5.4 DHCP reassigns printer IP after router restart
```
Problem: Printer was at 192.168.1.100. Router rebooted. Now it's at 192.168.1.105.
         Rasova still has 192.168.1.100 in Setup → all prints fail.

Fix: Set a STATIC (DHCP reservation) IP for the printer in the router settings.
     Most routers: Admin panel → DHCP → "Reserve IP" → enter printer's MAC address → assign fixed IP.
     Printer's MAC address: printed on the label on the bottom of the BillTouch printer.

ALWAYS do this before going live. DHCP leases expire and change.
```

### 5.5 Worker crashes mid-print
```
Scenario: Celery worker process is killed while bytes are being sent to printer.

Old behaviour (acks_on_receive): task was already acknowledged → lost forever.
New behaviour (acks_late=True):  task NOT acknowledged → automatically re-queued.

Worker restarts → picks up task again → would print twice!

Prevention: idempotency key in Redis.
  Before printing: check cache.get("bill_printed_{order_id}")
  Key exists? → already printed → skip → return True
  Key missing? → print → on success → write key (TTL: 2 hours)

Result: even after crash + restart, each order prints exactly once.
```

### 5.6 Worker restarts with a backlog of stale tasks
```
Scenario: Worker was down for 1 hour. 20 print jobs queued up in Redis.
          Worker comes back. Starts processing all 20.

Problem: printing 20 old bills at once confuses staff (food may already be served).

Prevention:
  CELERY_TASK_EXPIRES = 1800 (30 minutes) in settings.py
  Tasks older than 30 minutes are discarded automatically — never processed.
  
  Idempotency keys also help: if cashier manually reprinted during the downtime,
  the key exists and the task is silently skipped.
```

### 5.7 Two workers running for the same outlet (accidental)
```
Scenario: Someone starts the Celery worker twice on two different machines,
          both with RASOVA_OUTLET_ID=1.

Without idempotency: both workers pick up the same task → both try to print → double print.
With idempotency:    Worker A prints → writes Redis key.
                     Worker B picks up (if Celery re-delivers) → checks key → skips.

Note: Celery's task acknowledgement (acks_late) prevents the same task from
being delivered to two workers simultaneously. The idempotency key is a
second line of defence for edge cases.
```

### 5.8 Internet drops (Hyderabad server unreachable)
```
Scenario: Restaurant WiFi is up. Printer is up. But the internet is down.
          Django server in Hyderabad is unreachable.

Effect:
  - Cashier pays → Django can't respond (request times out)
  - Tasks can't be queued → no Celery task created
  - Fallback to sync print is triggered in billing_views.py:

  try:
      print_bill_task.delay(order_id, station_id)  ← fails: can't reach Redis
  except Exception:
      # Sync fallback: print immediately in the web request (slow, but works)
      printer.print_bill_with_kots(order, kots)

  This only works if Django itself is running locally (demo mode).
  In production (Hyderabad Django), the request itself would fail first.

For production: consider a local Django instance that only handles printing,
or queue to a local SQLite/Redis and sync to cloud when online.
```

### 5.9 Redis goes down
```
Scenario: Redis server on Hyderabad VPS crashes.

Effect:
  - New tasks can't be queued
  - print_bill_task.delay() raises ConnectionError
  - Fallback in pay_order() catches it → sync print (slow but works if local)
  - Worker can't read new tasks → no printing until Redis recovers

Recovery: Redis has automatic restart (systemctl enable redis). Usually up in seconds.
Monitor: add redis-cli ping to your uptime monitoring (UptimeRobot, BetterUptime).
```

### 5.10 Restaurant's WiFi changes (new router, new password)
```
Scenario: Restaurant switches ISP or router. Local worker loses connection to Redis.

Effect: Worker exits (connection refused). No printing.
        Workers do NOT auto-reconnect to Redis after extended disconnect.

Fix:
  Update REDIS_URL env var on local machine.
  Restart the Celery worker service.

Prevention: Use a systemd service for the worker (see Part 7).
            systemd will auto-restart on crash.
```

### 5.11 Task queue grows faster than printer can handle (busy service)
```
Scenario: 50 orders placed in 5 minutes. Printer prints 1 per 8 seconds.
          Queue grows to 400 seconds of backlog.

Effect: bills print 6 minutes after payment. Customers wait for receipts.

Fix: Each printer is independent. The queue only has tasks for ONE printer.
     If you have 2 printers, they each have their own station → own task → no bottleneck.
     For very high volume QSR, run 2 workers for the same outlet (idempotency prevents double print).
```

### 5.12 Printer too far from router (weak WiFi signal)
```
Symptom: Intermittent failures. Sometimes works, sometimes times out.

Fix:
  Option 1: Move printer or router.
  Option 2: Use Ethernet cable (preferred — more reliable than WiFi for printers).
  Option 3: WiFi extender between router and printer.
  Option 4: Use USB instead of network → USB only works from the local device.

BillTouch recommendation: always use Ethernet if the printer has an RJ45 port.
```

---

## Part 6 — Monday Demo: Exact Setup

```
YOUR LAPTOP (connected to restaurant WiFi)
┌───────────────────────────────────────────────────────────────────┐
│                                                                   │
│  Terminal 1: redis-server (or: docker run -p 6379:6379 redis)    │
│  Terminal 2: python manage.py runserver 0.0.0.0:8000             │
│  Terminal 3: celery -A core worker --loglevel=info -Q printing   │
│                                                                   │
│  Laptop LAN IP (check: ipconfig): e.g. 192.168.1.50             │
│                                                                   │
└───────────────────────────────┬───────────────────────────────────┘
                                │ WiFi (same router)
        ┌───────────────────────┼────────────────────────────┐
        ▼                       ▼                            ▼
BillTouch Printer       Restaurant owner's           Any staff tablet
192.168.1.100           phone / tablet               can open app
(find: FEED+power on)   Opens: 192.168.1.50:8000     on same WiFi
```

**Pre-demo checklist (do the night before):**

```
□ 1. Connect laptop to restaurant WiFi
□ 2. ipconfig → note LAN IP (e.g. 192.168.1.50)
□ 3. Hold FEED + power BillTouch → note IP from self-test printout
□ 4. Login: /superuser/ → Create restaurant → set type: QSR
□ 5. /superuser/tenant/<id>/ → enter printer IP → Save Printer
□ 6. Click preset: "QSR — no kitchen screen (print strip)"
□ 7. Verify features: token_system ON, kitchen_display OFF, inventory ON
□ 8. /menu/ → AI import (photo of their paper menu) or add manually
□ 9. python manage.py preview_print --list (pick a recent order)
□ 10. python manage.py preview_print <id> --strip --width 40
     → Confirms layout looks right BEFORE touching the real printer
□ 11. /setup/kitchen-stations/ → click "Test Print"
     → Confirms printer is reachable and paper feeds
□ 12. Create a test order end-to-end: token → add items → Pay → verify strip prints
□ 13. Show owner: /reports/dashboard/ → today's sales appear
□ 14. Show owner: /inventory/board/ → stock levels
```

**If test print fails:**
```
Connection refused:
  → Wrong IP. Re-do FEED+power → check IP on self-test → update in Setup.

Printer not found (timeout):
  → Not on same WiFi. Check printer's Ethernet vs WiFi.
  → Run: ping 192.168.1.100 from laptop terminal. If no reply → not reachable.

Garbled text / wrong characters:
  → Change Encoding in Setup from cp437 to utf-8 (or vice versa)

Paper feeds but nothing prints:
  → Paper loaded backwards. Thermal paper has a coated side that faces the print head.
  → Check: scratch the paper. The side that turns dark is the print side. That faces inward.
```

---

## Part 7 — Production Setup (after demo, real customers)

### Local device per restaurant

```
Hardware options:
  Raspberry Pi 4 (2GB)     ₹4,000  — best choice, silent, always on, tiny
  Old Android phone         ₹0      — use Termux app (Linux on Android)
  Spare Windows laptop      ₹0      — if already present at counter
  Mini PC (Intel NUC etc.) ₹8,000  — most powerful, runs Windows
```

### Windows auto-start (cashier laptop)

Create `rasova_worker.bat`:
```bat
@echo off
set RASOVA_TENANT_ID=1
set RASOVA_OUTLET_ID=1
set REDIS_URL=redis://:yourpassword@your-server.com:6379/0
set DJANGO_SETTINGS_MODULE=core.settings
cd /d C:\rasova
.venv\Scripts\celery.exe -A core worker --loglevel=info -Q printing,default
```

Add to Windows Task Scheduler → "At startup" → runs in background.

### Linux systemd service (Raspberry Pi / Ubuntu)

Create `/etc/systemd/system/rasova-worker.service`:
```ini
[Unit]
Description=Rasova Print Worker
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/rasova
Environment=RASOVA_TENANT_ID=1
Environment=RASOVA_OUTLET_ID=1
Environment=REDIS_URL=redis://:yourpassword@your-server.com:6379/0
Environment=DJANGO_SETTINGS_MODULE=core.settings
ExecStart=/home/pi/rasova/.venv/bin/celery -A core worker --loglevel=info -Q printing,default
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable rasova-worker
sudo systemctl start rasova-worker
sudo systemctl status rasova-worker  # check it's running
```

Auto-starts on boot. Auto-restarts if it crashes.

### Redis security for production

```
# .env on local machine
REDIS_URL=redis://:StrongPassword123@your-server.com:6379/0

# On server (redis.conf):
requirepass StrongPassword123
bind 0.0.0.0        # allow external connections
protected-mode no   # or configure proper firewall

# Firewall: allow port 6379 only from restaurant's static IP
# If restaurant has dynamic IP: use a VPN instead
```

**Ideal production setup: WireGuard VPN**
```
All restaurants connect to a WireGuard VPN on your Hyderabad server.
Redis listens only on the VPN interface (10.x.x.x).
No Redis port exposed to public internet.
Printers remain on restaurant LAN (192.168.x.x).
Each restaurant's local worker reaches Redis via VPN.
```

---

## Part 8 — Configuration Reference

### Environment variables (local worker)

| Variable | Required | Example | Purpose |
|---|---|---|---|
| `RASOVA_TENANT_ID` | Recommended | `1` | Only process this tenant's jobs |
| `RASOVA_OUTLET_ID` | Recommended | `1` | Only process this outlet's jobs |
| `REDIS_URL` | Required | `redis://:pass@server:6379/0` | Where to read tasks from |
| `DJANGO_SETTINGS_MODULE` | Required | `core.settings` | Django config |

### Celery settings (core/settings.py)

| Setting | Value | Why |
|---|---|---|
| `CELERY_TASK_ACKS_LATE` | `True` | Ack after completion, not receipt |
| `CELERY_TASK_REJECT_ON_WORKER_LOST` | `True` | Re-queue if worker dies mid-task |
| `CELERY_TASK_EXPIRES` | `1800` | Discard tasks older than 30 min |
| `CELERY_TASK_SOFT_TIME_LIMIT` | `30` | Raise exception after 30s |
| `CELERY_TASK_TIME_LIMIT` | `60` | Hard kill after 60s |

### KitchenStation setup (per restaurant, via /superuser/)

| Field | Recommended | Notes |
|---|---|---|
| `printer_ip` | Static IP | Set DHCP reservation in router |
| `printer_port` | `9100` | ESC/POS standard port, works on all brands |
| `paper_width_mm` | `80` for BillTouch | Check what roll is loaded (58mm or 80mm) |
| `cut_type` | `partial` | Safer — less jamming than full cut |
| `printer_encoding` | `cp437` | Standard for all Indian thermal printers |

---

## Part 9 — Summary Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         RASOVA PRINTING                                  │
│                                                                          │
│  CLOUD (Hyderabad)          INTERNET           LOCAL (Restaurant)        │
│  ┌───────────────┐                           ┌───────────────────────┐   │
│  │               │                           │                       │   │
│  │ Django        │  HTTP/HTTPS               │ Browser               │   │
│  │ handles web   │◄─────────────────────────►│ (owner's phone)       │   │
│  │ saves orders  │                           │                       │   │
│  │               │                           └───────────────────────┘   │
│  │ Redis         │  Redis TCP :6379                                       │
│  │ task queue    │◄─────────────────────────►┌───────────────────────┐   │
│  │               │  (tasks flow this way)    │ Celery Worker         │   │
│  │ PostgreSQL    │                           │ TENANT_ID=1           │   │
│  │ all data      │  PostgreSQL TCP :5432      │ OUTLET_ID=1           │   │
│  │               │◄─────────────────────────►│                       │   │
│  └───────────────┘                           └──────────┬────────────┘   │
│                                                         │                │
│                                                         │ LAN            │
│                                                         ▼                │
│                                               ┌───────────────────────┐  │
│                                               │  BillTouch Printer    │  │
│                                               │  192.168.1.100:9100   │  │
│                                               └───────────────────────┘  │
│                                                                          │
│  KEY:                                                                    │
│  ─── Cloud traffic (internet)    No private IP ever crosses internet.   │
│  LAN Local traffic (restaurant)  Printer only reachable from inside.    │
└─────────────────────────────────────────────────────────────────────────┘

DATA FLOW (one order, QSR strip mode):
  1. Owner pays on phone   → POST /pay/<id>/         → Django (cloud)
  2. Django saves payment  → print_bill_task.delay() → Redis (cloud)
  3. Redis delivers task   → Local Celery worker      (internet)
  4. Worker checks:           tenant_id matches? outlet_id matches?
  5. Worker checks:           Redis key "bill_printed_42"? → no → proceed
  6. Worker connects:         Network("192.168.1.100", 9100)  (local LAN)
  7. Worker sends:            ESC/POS bytes for receipt + KOTs
  8. Printer cuts:            partial cuts between sections, FULL CUT at end
  9. Worker writes:           Redis key "bill_printed_42" (2h TTL)
  10. Strip comes out:        Customer carries receipt+KOTs to food counter
```
