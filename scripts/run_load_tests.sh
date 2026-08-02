#!/bin/bash
# Convenience wrapper around the two load-test management commands.
#
# Phase A (load_test) is DB-level only -- safe to run locally against your
# own Postgres, no server needed, always runs.
# Phase B (http_rush_test) fires real HTTP requests at a running server --
# only runs if you pass --host. See http_rush_test.py's own docstring for
# why the capacity numbers only mean something for whatever machine that
# server is actually running on.
#
# Usage:
#   bash scripts/run_load_tests.sh
#   bash scripts/run_load_tests.sh --host http://127.0.0.1:8000
#   bash scripts/run_load_tests.sh --host http://<ec2-ip>:8000 --allow-remote
#
# Env vars (all optional):
#   ORDERS, WORKERS, RACE_WORKERS                  -- load_test.py sizing
#   RUSH_TENANTS, RUSH_PER_TENANT, RUSH_WORKERS    -- http_rush_test.py sizing
#   KEEP=1                                          -- skip cleanup on both (pass --keep)
set -e

HOST=""
ALLOW_REMOTE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --allow-remote) ALLOW_REMOTE="--allow-remote"; shift ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [ -f .venv/Scripts/activate ]; then
    source .venv/Scripts/activate
elif [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
else
    echo "No .venv found -- run this from the repo root."
    exit 1
fi

KEEP_FLAG=""
[ "${KEEP:-0}" = "1" ] && KEEP_FLAG="--keep"

echo "=== Phase A: DB-level concurrency + race probes (load_test) ==="
python manage.py load_test \
    --orders "${ORDERS:-200}" \
    --workers "${WORKERS:-12}" \
    --race-workers "${RACE_WORKERS:-30}" \
    $KEEP_FLAG

if [ -n "$HOST" ]; then
    echo ""
    echo "=== Phase B: HTTP-level multi-tenant rush (http_rush_test) ==="
    python manage.py http_rush_test \
        --host "$HOST" \
        --tenants "${RUSH_TENANTS:-8}" \
        --requests-per-tenant "${RUSH_PER_TENANT:-15}" \
        --workers "${RUSH_WORKERS:-20}" \
        $KEEP_FLAG $ALLOW_REMOTE
else
    echo ""
    echo "No --host given -- skipped the HTTP rush test. Pass --host <url> to also test a running server."
fi

echo ""
echo "Done. Full run logs are under loadtest_logs/ (see the 'Full log saved to:' line above for each phase)."
