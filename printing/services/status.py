"""
What the cashier's screen says about printing (B12, 13 Nov 2026).

Printing is what fails most in a real restaurant: the print app on the
counter phone was closed, the phone slept, the printer is off or out of
paper. The cashier used to find out when no paper came out. Now the
8-second notifications answer (orders.api.notification_api) carries the
outlet's printing state, and the billing, tables and token screens show it
as a chip with the one thing to do (static/js/core/printing_status.js).

The state is facts only: when an agent last asked for jobs, how many jobs
are waiting and for how long, how many failed in the last hour and the
last error. The words, and what depends on the device (a desktop's own
agent, an iPhone's AirPrint), are the screen's job.
"""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from printing.models import PrintJob
from tenants.models import Outlet

SEEN_EVERY = 60            # seconds between two writes of print_agent_seen_at
FAILED_WINDOW = timedelta(hours=1)


def record_agent_seen(outlet, now=None):
    """An agent with this outlet's key just polled. Written to the database
    at most once a minute: the poll comes every 2 seconds and an idle one
    is meant to cost a single cache read (printing.views.print_queue_poll).
    True when this call wrote it."""
    try:
        first_this_minute = cache.add(f"printq:seen:{outlet.id}", 1, timeout=SEEN_EVERY)
    except Exception:  # noqa: BLE001 -- cache down: write, as the poll itself falls back to the database
        first_this_minute = True
    if not first_this_minute:
        return False
    Outlet.objects.filter(pk=outlet.pk).update(print_agent_seen_at=now or timezone.now())
    return True


def _seconds(delta):
    return max(0, int(delta.total_seconds()))


def printing_state(outlet, now=None):
    """The facts the printer chip is drawn from, for one outlet."""
    now = now or timezone.now()
    seen = Outlet.objects.filter(pk=outlet.pk).values_list("print_agent_seen_at", flat=True).first()
    jobs = PrintJob.objects.for_outlet(outlet.tenant_id, outlet.pk)
    waiting = jobs.filter(status__in=[PrintJob.PENDING, PrintJob.PROCESSING])
    oldest = waiting.order_by("created_at").values_list("created_at", flat=True).first()
    failed = jobs.filter(status=PrintJob.FAILED, done_at__gte=now - FAILED_WINDOW)
    last_error = failed.order_by("-done_at").values_list("error_msg", flat=True).first()
    return {
        "agent_seen_seconds": _seconds(now - seen) if seen else None,
        "waiting": waiting.count(),
        "oldest_seconds": _seconds(now - oldest) if oldest else None,
        "failed_recent": failed.count(),
        "last_error": (last_error or "")[:160],
    }
