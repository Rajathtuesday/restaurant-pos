"""
Shared helpers for the load_test / http_rush_test management commands.

Not a command itself (leading underscore keeps Django's command loader from
picking it up). Three pieces:
  - RunLogger:      tees every line to stdout AND a timestamped log file, so
                     a run's full output can be reread later without having
                     watched it live.
  - ResourceSampler: background-thread CPU/memory sampling of the process
                     THIS script runs in -- honest about what it does and
                     doesn't tell you (see the docstring on the class).
  - db_snapshot:     a plain SQL snapshot of Postgres connection/lock state,
                     works identically whether Postgres is local or remote
                     since it's just a query over the existing connection.
"""
import os
import re
import statistics
import threading
from datetime import datetime

from django.db import connection

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class RunLogger:
    """Writes every line to both stdout (via the passed `stdout_write`
    callable, e.g. a Command's self.stdout.write -- ANSI colors intact) and
    a timestamped plain-text file under log_dir (ANSI stripped), so results
    survive after the terminal is gone and are still readable in a text
    editor."""

    def __init__(self, stdout_write, log_dir, prefix):
        self._stdout_write = stdout_write
        os.makedirs(log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = os.path.join(log_dir, f"{prefix}_{ts}.log")
        self._fh = open(self.path, "w", encoding="utf-8")
        self._fh.write(f"Run started: {datetime.now().isoformat()}\n\n")

    def write(self, line=""):
        self._stdout_write(line)
        self._fh.write(_ANSI_RE.sub("", str(line)) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.write(f"\nRun finished: {datetime.now().isoformat()}\n")
        self._fh.close()


class ResourceSampler:
    """Samples CPU% / memory of the CURRENT process roughly once a second
    on a background thread, for the duration of a phase.

    Honest caveat, stated here so every caller inherits it: this reflects
    whatever machine the script is *run from*. Run locally against a local
    dev server, it tells you about your dev machine, not the app server.
    For real server capacity numbers this needs to run on (or watched
    alongside, via a separately-run `top`/`free -h`) the actual host.
    """

    def __init__(self, interval=1.0):
        self._interval = interval
        self._stop = threading.Event()
        self._cpu, self._mem_mb = [], []
        self._thread = None
        try:
            import psutil
            self._proc = psutil.Process()
        except ImportError:
            self._proc = None

    def start(self):
        if self._proc is None:
            return self
        self._proc.cpu_percent()  # first call primes the internal baseline
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            try:
                self._cpu.append(self._proc.cpu_percent())
                self._mem_mb.append(self._proc.memory_info().rss / (1024 * 1024))
            except Exception:
                pass
            self._stop.wait(self._interval)

    def stop(self):
        if self._proc is None:
            return {"available": False}
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if not self._cpu:
            return {"available": True, "samples": 0}
        return {
            "available": True,
            "samples": len(self._cpu),
            "cpu_pct_mean": round(statistics.mean(self._cpu), 1),
            "cpu_pct_max": round(max(self._cpu), 1),
            "mem_mb_mean": round(statistics.mean(self._mem_mb), 1),
            "mem_mb_max": round(max(self._mem_mb), 1),
        }


def cleanup_tenant(tenant):
    """Delete a tenant and everything under it, honoring the PROTECT FK chain
    (Payment.order and Order.tenant are both on_delete=PROTECT, so payments
    must go before orders, and orders before the tenant). Shared by every
    load-test command so a throwaway tenant never lingers."""
    from orders.models import Order, Payment
    from payments.models import Refund
    from tenants.models import Tenant
    Refund.objects.filter(order__tenant=tenant).delete()
    Payment.objects.filter(order__tenant=tenant).delete()
    Order.objects.filter(tenant=tenant).delete()
    Tenant.objects.filter(pk=tenant.pk).delete()


def is_safe_host(host):
    """True if `host` looks like localhost or a private-network address --
    used to gate --allow-remote before any HTTP load test fires a single
    request at it. Deliberately conservative: anything not obviously
    local/private is treated as unsafe (including real domains like
    rasova.net) and requires an explicit opt-in."""
    from urllib.parse import urlparse
    import ipaddress
    hostname = urlparse(host).hostname or ""
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False


def db_snapshot():
    """Point-in-time Postgres connection/lock state. No-ops (returns None)
    on SQLite, where these system views don't exist in the same form."""
    if "postgresql" not in connection.vendor:
        return None
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        )
        active_connections = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
        waiting_locks = cur.fetchone()[0]
    return {"active_connections": active_connections, "waiting_locks": waiting_locks}


def format_resource_line(label, res):
    if res is None:
        return f"{label}: (skipped -- not Postgres)"
    if not res.get("available"):
        return f"{label}: (psutil not installed -- run `pip install psutil` for process sampling)"
    if res.get("samples", 0) == 0:
        return f"{label}: (phase too short to sample)"
    return (
        f"{label}: cpu mean={res['cpu_pct_mean']}% max={res['cpu_pct_max']}%  "
        f"mem mean={res['mem_mb_mean']}MB max={res['mem_mb_max']}MB  "
        f"({res['samples']} samples)"
    )
