# ELI5: Celery and Redis
**Explain Like I'm 5 — but in actual detail, with your actual code.**

---

## The problem first. What is broken right now.

When a cashier at Rasova clicks **Collect Payment**, this is what Django does:

```
1. Save payment to database          ← fast, ~5ms
2. Update order status to "closed"   ← fast, ~5ms
3. Connect to thermal printer        ← SLOW. Up to 5 seconds if printer lags
4. Send bill data to printer         ← SLOW. Another 2-3 seconds
5. Connect to printer again for KOT  ← SLOW again
6. Send KOT data                     ← SLOW again
7. ONLY NOW: respond to the browser  ← cashier has been staring at a spinner
```

The cashier is frozen for 10-15 seconds. In a busy restaurant at dinner service,
that is unacceptable. And if the printer is unreachable (paper jam, wrong IP,
printer off), the request times out completely and the cashier sees an error.

The payment WAS saved. The order IS closed. But the cashier thinks it failed.
So they try again. Now you have a double charge.

**This is the problem Celery and Redis solve.**

---

## The analogy. Your own restaurant.

Imagine you are the cashier at a counter.

A customer gives you their order. You could:

**Option A (what Django does now):**
Walk to the kitchen yourself. Stand there and watch the chef cook. Wait until the
food is ready. Walk back to the counter. Only then take the next customer's order.
The queue behind you grows to 20 people. Everyone is angry.

**Option B (what Celery + Redis does):**
Write the order on a slip. Put the slip on the ticket rail. Say "Next!" to the
customer. Walk away. Someone else (the kitchen) will handle it. You are free
immediately.

That slip on the ticket rail — that is **Redis**.
The kitchen staff who picks up the slip and does the work — that is **Celery**.

---

## What Redis actually is

Redis stands for **Re**mote **Di**ctionary **S**erver. That name tells you almost
nothing useful. Here is what it actually is:

**Redis is a list that lives in RAM, accessible over a network.**

That is it. A very fast list. When Celery needs to hand off a task, it writes a
message to Redis: "Hey, someone needs to print KOT #47 on the printer at 192.168.1.100."
That message sits in Redis until a Celery worker picks it up.

Redis is fast because it lives in memory, not on disk. Reading from RAM is
roughly 1000x faster than reading from a hard drive. A message can be written
to Redis and read back in under 1 millisecond.

Redis is not a database. It does not persist data the way PostgreSQL does.
It is a temporary holding area. Messages go in, get processed, and disappear.

In your project, Redis serves two purposes:
1. **Celery's message broker** — stores pending tasks (print this KOT, send this email)
2. **Django's cache backend** — already being used for printer error banners

---

## What Celery actually is

Celery is a **separate Python process** that runs alongside Django.

When you start your server normally, you run one process:
```
python manage.py runserver    ← this is Django, handling web requests
```

With Celery, you run two:
```
python manage.py runserver    ← Django (handles HTTP requests, talks to browser)
celery -A core worker         ← Celery (reads from Redis, runs background tasks)
```

These two processes run simultaneously but independently. Django handles the web
browser. Celery handles everything that should not block the web browser.

Celery's job is simple: wake up, check Redis for pending tasks, run them one
by one, mark them done, go back to sleep. Repeat forever.

When Django wants to hand off work to Celery, it does this:
```python
# Old way (runs RIGHT NOW, blocking the request):
print_kot_task(station_id=1, order_id=42, kot_id=7)

# New way (sends to Redis, returns immediately, Celery runs it):
print_kot_task.delay(station_id=1, order_id=42, kot_id=7)
```

The `.delay()` call takes ~1 millisecond. Django is immediately free to respond
to the browser. A second later, Celery picks up the task and does the actual work.

---

## How they fit together in Rasova

Here is the full picture after we add Celery and Redis:

```
CASHIER CLICKS "COLLECT PAYMENT"
           │
           ▼
    Django receives request
           │
           ├── Save payment to DB          ← 5ms
           ├── Update order to "closed"    ← 5ms
           ├── Write "print this KOT"      ← 1ms
           │   message to Redis
           │
           ▼
    Django responds to browser             ← Total: ~12ms
    "Payment done. ✓"
    Cashier sees success immediately.
           │
           │   (in parallel, completely separate)
           │
           ▼
    Celery worker reads from Redis
           │
           ├── Connects to printer
           ├── Prints bill
           ├── Prints KOT 1 (partial cut)
           ├── Prints KOT 2 (partial cut)
           │
           ▼
    Task complete. Celery marks it done.
    If printer failed: stores error in Redis cache
    so the "Printer Failure" banner appears.
```

The cashier never waits for the printer. The browser gets its response in ~12ms
instead of 15 seconds. If the printer is unreachable, the task fails in the
background and shows a banner — but the order is saved correctly and the cashier
already moved on to the next customer.

---

## What tasks Rasova will run through Celery

After this session, these things move off the web thread:

| Task | Was | Now |
|---|---|---|
| Print KOT after order sent to kitchen | Daemon thread in request | Celery task |
| Print bill + KOTs after payment | Synchronous in request | Celery task |
| Send WhatsApp bill (if configured) | Not built | Celery task |
| Clear printer error after fix | Manual | Celery beat (scheduled) |

---

## The "worker" and the "beat" — two types of Celery

**Celery Worker:** Sits and waits for tasks. When a task arrives, runs it immediately.
This is what handles printing.

**Celery Beat:** A scheduler. Runs tasks on a timer, like a cron job.
For example: "every 5 minutes, clear stale printer errors."
You need to run this as a third process if you use scheduled tasks.

For now, we only need the worker.

---

## How Redis knows which tasks belong to which worker

Redis uses **queues** — named lists. You can have:
- `default` queue — general tasks
- `printing` queue — only print tasks (so a slow print job never blocks other tasks)

Celery workers can listen to one queue or many. You can run multiple workers.
You can say "this worker only handles printing, this other one handles emails."

For Rasova right now, one worker on the default queue is enough.
You can split queues later when you have 50+ restaurants.

---

## What happens if Redis goes down

If Redis is unreachable when Django tries to queue a task:
- Django throws an exception
- The task is lost

This is the tradeoff: you gain speed and reliability during normal operation,
but you add a dependency. Redis going down means tasks stop queuing.

In practice, Redis is extremely stable. It almost never crashes.
On a VPS, if Redis goes down, your database is also probably down.
You have bigger problems.

The fallback for Rasova: if the Celery task fails to queue, we catch the exception
and fall back to the old synchronous printing (slow but it still works).

---

## Installing Redis on your machine (Windows)

Redis doesn't have an official Windows build. Three options:

**Option 1: WSL (recommended for development)**
```bash
# In WSL terminal:
sudo apt-get install redis-server
redis-server
```

**Option 2: Docker (cleanest)**
```bash
docker run -d -p 6379:6379 redis:alpine
```

**Option 3: Memurai (Windows Redis port)**
Download from memurai.com — free for development, paid for production.

On your production server (Ubuntu/Debian):
```bash
sudo apt-get install redis-server
sudo systemctl enable redis
sudo systemctl start redis
```

Redis runs on port 6379 by default.
To test it is running: `redis-cli ping` → should reply `PONG`.

---

## Running everything locally after setup

You will need THREE terminal windows:

```bash
# Terminal 1: Django
python manage.py runserver

# Terminal 2: Celery worker
celery -A core worker --loglevel=info

# Terminal 3: Redis (if not running as a service)
redis-server
```

In production (on your VPS), all three run as systemd services so they
start automatically and restart if they crash.

---

## The code changes — what actually changes in Rasova

**Before (tasks.py):**
```python
def print_kot_task(station_id, order_id, kot_id):
    # regular function, called directly
    ...
```

**After (tasks.py):**
```python
from celery import shared_task

@shared_task(bind=True, max_retries=3)
def print_kot_task(self, station_id, order_id, kot_id):
    # now a Celery task, called with .delay()
    ...
```

**Before (kot_service.py):**
```python
threading.Thread(
    target=print_kot_task,
    args=(station.id, order.id, kot.id),
    daemon=True
).start()
```

**After (kot_service.py):**
```python
print_kot_task.delay(station.id, order.id, kot.id)
# One line. No threads. Returns in 1ms.
```

That is the entire change from the outside. Everything else is configuration.

---

## Summary

| Thing | What it is | Analogy |
|---|---|---|
| The problem | Printing blocks the web request | Cashier walks to kitchen herself |
| Redis | A fast in-memory list/queue | The ticket rail in the kitchen |
| Celery | A separate Python process that reads tasks | The kitchen staff |
| `.delay()` | Sends a task to Redis instead of running it | Writing an order slip |
| Celery worker | The process that processes tasks | The cook who reads slips |
| Celery beat | Scheduled task runner | Prep cook who works on a timer |

**The result:** Cashier clicks "Collect Payment". Browser responds in 12ms.
Printer does its job 1 second later, in the background, independently.
If the printer fails, the order is still saved and the cashier already
moved to the next customer.
