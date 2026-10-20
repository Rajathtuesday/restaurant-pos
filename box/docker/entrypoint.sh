#!/bin/sh
# What the box image runs. "web": wait for the database, apply migrations
# (one container at a time: a Postgres advisory lock inside migrate is not
# needed because only the web container migrates), then serve. "worker":
# Celery with its scheduler (the nightly backup, printing).
set -eu

wait_for_db() {
  i=0
  until python -c "import django;django.setup();from django.db import connection;connection.ensure_connection()" 2>/dev/null; do
    i=$((i+1))
    if [ "$i" -gt 60 ]; then echo "database did not come up" >&2; exit 1; fi
    sleep 2
  done
}

case "${1:-web}" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    exec gunicorn core.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_WORKERS:-3}" \
         --timeout 60 --graceful-timeout 20 --access-logfile - --error-logfile -
    ;;
  worker)
    wait_for_db
    # Both queues, as in the cloud: "printing" carries the KOTs and bills
    # (the box is on the restaurant's network, so it prints straight to the
    # printers' IPs), "default" everything else.
    exec celery -A core worker -B --queues default,printing --concurrency "${WORKER_CONCURRENCY:-2}" \
         --schedule /data/celerybeat-schedule --loglevel warning
    ;;
  manage)
    shift
    wait_for_db
    exec python manage.py "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
