# It is Saturday, 9 pm, and...

Written while calm, to be read while not. One page per trouble: how to tell,
the two things to try, and when to stop trying and undo or restore.

Everything below runs on the server, in the app folder:

```bash
ssh ubuntu@<server>            # or AWS Console > EC2 > Connect > EC2 Instance Connect
cd /home/ubuntu/rasova
```

**First, always:** what does the server say about itself?

```bash
curl -s -H "X-Health-Token: $(grep ^HEALTH_TOKEN= .env | cut -d= -f2-)" http://localhost:8000/health/ | python3 -m json.tool
```

Each check says `ok: true`, `false` (act on it) or `null` (does not apply).
The line that says `false` tells you which section below to read.
No answer at all: read "The site is down".

**Two rules for the night:**

1. **Did a deploy go out in the last hour?** Then undo it before anything else:
   `bash deploy.sh --undo` (about 30 seconds). Fix it tomorrow.
2. **Never restore the database to fix a slow or broken site.** A restore loses
   every bill since the backup. Restore only when the data itself is gone or
   wrong (section 6).

---

## 1. The site is down

**How to tell:** the browser shows a Cloudflare page. 502 means the app is
not answering; 521 or 522 means the server itself is not answering.

```bash
sudo systemctl status gunicorn nginx postgresql redis-server --no-pager | grep -E "^.*\.service|Active:"
tail -40 logs/gunicorn_error.log
```

Try, in this order:

1. `sudo systemctl restart gunicorn`, wait 10 seconds, ask `/health/` again.
2. One of the four is not `active (running)`: `sudo systemctl restart <it>`.

Cannot SSH in at all (521, 522): AWS Console > EC2 > the instance. Status
checks failing or the instance stopped: **Reboot** (not Stop: a stop can
change the address). Wait two minutes, then come back to this page.

**Undo** if it went down after a deploy. **Never restore** for this.

## 2. Printing stopped

**How to tell:** the printer chip on the billing screen is not green; tap it,
it says what is wrong. `/health/`'s `printing` line says how many jobs wait.

In the restaurant, first (most printing faults are here, not on the server):

1. The printer: on, paper in, cover shut, its light steady.
2. The phone that prints (the Rasova app): open the app, same Wi-Fi as the
   printer, not in battery saver. Then Setup > Printers > **Test Print**.

On the server, only if test prints never arrive:

```bash
sudo systemctl status celery --no-pager | grep Active:
tail -40 logs/celery.log
```

`sudo systemctl restart celery` if it is not running.

**Meanwhile:** the bill page prints through the phone's or the laptop's own
browser (the receipt page opens). Service goes on; the kitchen can read KOTs
from the kitchen screen.

## 3. Payments fail

**How to tell:** the cashier taps Pay and gets "Payment could not be
processed", or the bill stays open.

```bash
grep -E "payment|Payment" logs/errors.log | tail -20
tail -20 logs/gunicorn_error.log
```

1. Reload the bill page: did the payment go through after all? The bill page
   lists every payment taken. **Never take a payment twice.**
2. "Order already completed": it did go through. "No open cash session" or a
   closed shift: open the register (Cash Register) and try again.

**Meanwhile:** take the money, write the bill number and amount on paper,
record it when the screen works. A payment taken offline syncs by itself; one
that says "could not be synced" may have gone through: check the bill page
before recording it again.

If every payment fails after a deploy: **undo**.

## 4. The disk is full

**How to tell:** `/health/` says `disk: false`, or pages fail with errors
about writing files.

```bash
df -h /
sudo du -xsh logs versions /var/log/journal /var/lib/postgresql/*/main/pg_wal 2>/dev/null | sort -h
```

1. Logs: `bash scripts/clean_logs.sh` and `sudo journalctl --vacuum-size=200M`.
2. `pg_wal` large (gigabytes): WAL archiving to R2 has stopped and Postgres is
   keeping the files until it works. **Do not delete anything in pg_wal.** Run
   `sudo -u postgres .venv/bin/python scripts/backup/check_wal_archiving_health.py`
   and read what it says; this one can wait for daylight once the disk has room.

`versions/last/before.sql.gz` is the last deploy's database copy: it may go
if you need the space, and the next deploy writes it again.

## 5. Everything is slow

**How to tell:** pages take seconds, the kitchen screen lags, `/health/` says
`swap: false` or `connections: false`.

```bash
uptime; free -m
grep "slow query" logs/errors.log | tail -20
```

1. `top`: a high `st` (steal) figure means the server has used up its CPU
   credits (a t3.micro does after a busy stretch). Nothing to fix tonight; it
   recovers as the load drops. Resize to a larger instance tomorrow.
2. One query stuck: see what Postgres is doing, and cancel the longest:

```bash
sudo -u postgres psql -c "SELECT pid, now() - query_start AS running, state, left(query, 80) FROM pg_stat_activity WHERE state <> 'idle' ORDER BY running DESC NULLS LAST LIMIT 10;"
sudo -u postgres psql -c "SELECT pg_cancel_backend(<pid>);"
```

A web statement is cancelled by itself after 15 seconds (`docs/DEPLOY.md`,
"Database limits"); one running longer is a migration, Celery or the backup.
Still slow after that: `sudo systemctl restart gunicorn celery`.

## 6. When to restore

Only when the data is gone or wrong: a table emptied, a migration that broke
rows, a server that will not come back. Not for a slow, down or buggy site.

1. Stop taking orders on the screens (paper for the rest of the night).
2. The last deploy's own copy, if a migration broke it: `versions/last/before.sql.gz`.
   Otherwise last night's backup: `docs/MEDIA_AND_BACKUPS.md`, section 4
   ("Restore from a backup"); to a minute before the damage: section 4b.
3. Restore into a spare database first (`scripts/backup/restore_drill.py --keep`
   shows the way), check it, then switch.

Everything since the backup is lost unless it is in the WAL archive (4b).
Write down the night's bills on paper so they can be entered again.

## After the night

Write two lines in `CHANGELOG.md` or a note: what broke, what fixed it.
The next time is shorter.
