#!/bin/bash
# Keep Rasova's append-only logs from growing forever.
#
# Django app logs (pos.log, errors.log, django.log, security.log) already self-cap
# via Python's RotatingFileHandler — this script only handles the plain-append logs
# that nothing rotates: celery.log and backup.log.
#
# Strategy: if a log is bigger than MAX_BYTES, keep only the most recent KEEP_BYTES
# (in-place truncate, safe while celery/cron is still appending — no restart needed).
#
# Run by hand:   bash scripts/clean_logs.sh
# Weekly cron:   0 3 * * 0 cd /home/ubuntu/rasova && bash scripts/clean_logs.sh >> logs/clean_logs.log 2>&1
set -e

LOG_DIR="${LOG_DIR:-/home/ubuntu/rasova/logs}"
MAX_BYTES=$((10 * 1024 * 1024))   # trim once a log passes 10 MB
KEEP_BYTES=$((2 * 1024 * 1024))   # keep the most recent 2 MB

for name in celery.log backup.log clean_logs.log; do
    f="$LOG_DIR/$name"
    [ -f "$f" ] || continue
    size=$(stat -c%s "$f" 2>/dev/null || echo 0)
    if [ "$size" -gt "$MAX_BYTES" ]; then
        tail -c "$KEEP_BYTES" "$f" > "$f.tmp" && cat "$f.tmp" > "$f" && rm -f "$f.tmp"
        echo "$(date '+%F %T') trimmed $name ($((size/1024/1024)) MB -> $((KEEP_BYTES/1024/1024)) MB)"
    fi
done

# Safety net: delete any stray rotated files older than 30 days (RotatingFileHandler
# already caps its own .log.1..N, so this is just for anything left behind).
find "$LOG_DIR" -name '*.log.*' -type f -mtime +30 -delete 2>/dev/null || true

echo "$(date '+%F %T') log cleanup done."
