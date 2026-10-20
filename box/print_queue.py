"""
On a box, the print queue is printed by the box itself.

In the cloud, a print job (Setup's test print, a bill pushed from the
Android app) waits in printing.PrintJob until a device in the restaurant
(the phone app, the PC agent) collects it, because the cloud can't reach a
printer on the restaurant's network. A box IS on that network, so its own
worker sends each new job straight to the printer's IP (box/signals.py
queues it). It claims the job the way an agent does (PENDING to PROCESSING
in one UPDATE), so if an agent is also pointed at the box, a job still
prints once.
"""
import base64
import logging
import socket
import time
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger("pos.printing")

# A job still PROCESSING this long after it was claimed was dropped by a
# worker that died mid-print: it may be claimed again (the agents' poll uses
# the same idea for a phone that died).
STALE_CLAIM = timedelta(minutes=2)


def print_job(job_id, tries=2, wait=2.0):
    """Send one queued job to its printer. True if it printed; False if
    someone else has it, it is done, or the printer didn't answer (then the
    job is FAILED with the reason)."""
    from printing.models import PrintJob
    now = timezone.now()
    claimable = Q(status=PrintJob.PENDING) | Q(status=PrintJob.PROCESSING, claimed_at__lt=now - STALE_CLAIM)
    if not PrintJob.objects.filter(claimable, pk=job_id).update(status=PrintJob.PROCESSING, claimed_at=now):
        return False
    payload = PrintJob.objects.values_list("payload", flat=True).get(pk=job_id)
    try:
        data = base64.b64decode(payload["data_b64"])
        host = str(payload["network_host"])
        port = int(payload.get("network_port") or 9100)
    except (KeyError, TypeError, ValueError) as e:
        _finish(job_id, error=f"The print job is incomplete ({e}).")
        return False
    last = ""
    for attempt in range(tries):
        try:
            with socket.create_connection((host, port), timeout=10) as conn:
                conn.sendall(data)
            _finish(job_id)
            return True
        except OSError as e:
            last = f"{type(e).__name__}: {e}"
            if attempt + 1 < tries:
                time.sleep(wait)
    logger.warning("Box print job %s: printer %s:%s didn't answer (%s)", job_id, host, port, last)
    _finish(job_id, error=f"The printer at {host}:{port} didn't answer ({last})."[:512])
    return False


def _finish(job_id, error=""):
    from printing.models import PrintJob
    PrintJob.objects.filter(pk=job_id, status=PrintJob.PROCESSING).update(
        status=PrintJob.FAILED if error else PrintJob.DONE, done_at=timezone.now(), error_msg=error)
