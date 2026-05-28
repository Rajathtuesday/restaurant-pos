"""
Print queue — server-side job list consumed by Rasova Agent in polling mode.

Browser (Android) → POST /orders/agent/add-job/   (CSRF, adds PrintJob row)
Agent             → GET  /orders/agent/<key>/jobs/ (no CSRF, auth via outlet key)
Agent             → POST /orders/agent/<key>/done/<id>/  (marks job done)

The agent polls every 2 s using plain HTTP — no WebSocket, no inbound port,
no firewall issues.  Android cannot kill an outbound HTTP loop the same way
it kills a listening WebSocket server.

Multi-device safety: poll atomically claims jobs (PENDING → PROCESSING) so two
devices polling the same outlet never print the same job.  A device that crashes
mid-print is recovered after 2 minutes via the stale-claim reset at poll time.
"""
import logging
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db import connection, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.decorators import tenant_required
from orders.models import Order, PrintJob
from tenants.models import Outlet

logger = logging.getLogger("pos.orders")

# Jobs older than this are considered stale and not served to the agent
_JOB_TTL = timedelta(minutes=5)
# Device that claimed a job is considered crashed after this long without done/failed
_CLAIM_TTL = timedelta(minutes=2)


# ── Browser side ──────────────────────────────────────────────────────────────

@login_required
@require_POST
@tenant_required
def print_queue_add(request):
    """
    Browser calls this to enqueue a receipt print job.
    Generates ESC/POS server-side so the browser doesn't need to fetch qz-data first.
    """
    import json
    try:
        body = json.loads(request.body)
        order_id = int(body.get("order_id", 0))
    except Exception:
        return JsonResponse({"error": "Invalid request"}, status=400)

    try:
        order = Order.objects.prefetch_related(
            "items__menu_item__category", "payments", "token"
        ).get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)

    # Get station config (printer IP, paper width, encoding)
    try:
        from setup.services.station_service import get_default_station
        station = get_default_station(request.user)
    except Exception:
        station = None

    printer_ip   = station.printer_ip   if station and station.printer_ip   else ""
    printer_port = station.printer_port if station and station.printer_port else 9100
    encoding     = station.printer_encoding if station else "cp437"
    chars        = station.chars_per_line   if station else 48
    cut          = station.cut_type         if station else "full"

    if not printer_ip:
        return JsonResponse({"error": "No printer configured for this outlet"}, status=422)

    # Build ESC/POS bytes and store as base64 so PostgreSQL jsonb doesn't choke
    # on raw control characters (\x1B ESC, \x1D GS, etc.)
    try:
        data_b64 = _build_receipt_b64(order, chars, cut, encoding)
    except Exception as e:
        logger.exception("print_queue_add: ESC/POS build failed for order %s", order_id)
        return JsonResponse({"error": "Could not generate receipt"}, status=500)

    job = PrintJob.objects.create(
        tenant  = request.user.tenant,
        outlet  = request.user.outlet,
        payload = {
            "data_b64":     data_b64,
            "network_host": printer_ip,
            "network_port": printer_port,
            "encoding":     encoding,
        },
    )
    logger.info("PrintJob #%d queued for order %d (outlet %d)", job.pk, order_id, request.user.outlet.pk)
    return JsonResponse({"success": True, "job_id": job.pk})


# ── Agent side (no CSRF — auth via secret key in URL) ─────────────────────────

@csrf_exempt
@require_GET
def print_queue_poll(request, agent_key):
    """
    Agent calls this every 2 s to fetch pending jobs.
    Auth: agent_key must match an Outlet.print_agent_key UUID.
    Returns at most 5 jobs at a time.

    Atomic claim: jobs move PENDING → PROCESSING inside a transaction.
    PostgreSQL uses SELECT FOR UPDATE SKIP LOCKED so two concurrent polls
    never receive the same job.  Stale PROCESSING jobs (device crashed) are
    reset to PENDING automatically after _CLAIM_TTL.
    """
    outlet = _outlet_by_key(agent_key)
    if outlet is None:
        return JsonResponse({"error": "Invalid key"}, status=403)

    now = timezone.now()
    job_ids = []

    with transaction.atomic():
        # Reset stale claims: device died before calling done/failed
        reset = PrintJob.objects.filter(
            tenant_id=outlet.tenant_id,
            outlet=outlet,
            status=PrintJob.PROCESSING,
            claimed_at__lt=now - _CLAIM_TTL,
        ).update(status=PrintJob.PENDING, claimed_at=None)
        if reset:
            logger.warning(
                "PrintQueue: outlet %d — %d stale PROCESSING job(s) reset to PENDING (device crashed mid-print)",
                outlet.pk, reset,
            )

        # Atomically claim up to 5 pending jobs
        qs = PrintJob.objects.filter(
            tenant_id=outlet.tenant_id,
            outlet=outlet,
            status=PrintJob.PENDING,
            created_at__gte=now - _JOB_TTL,
        ).order_by("created_at")

        # PostgreSQL: SKIP LOCKED skips rows another transaction already locked —
        # two concurrent polls will never claim the same job.
        # SQLite (dev/test): plain SELECT FOR UPDATE — no real concurrency in dev.
        if connection.vendor == "postgresql":
            qs = qs.select_for_update(skip_locked=True)
        else:
            qs = qs.select_for_update()

        job_ids = list(qs.values_list("pk", flat=True)[:5])

        if job_ids:
            PrintJob.objects.filter(pk__in=job_ids).update(
                status=PrintJob.PROCESSING,
                claimed_at=now,
            )
            logger.info(
                "PrintQueue: outlet %d claimed %d job(s) %s",
                outlet.pk, len(job_ids), job_ids,
            )

    jobs = PrintJob.objects.filter(pk__in=job_ids) if job_ids else []
    return JsonResponse({
        "jobs": [
            {
                "id":           j.pk,
                "network_host": j.payload.get("network_host", ""),
                "network_port": j.payload.get("network_port", 9100),
                "data_b64":     j.payload.get("data_b64", ""),
            }
            for j in jobs
        ]
    })


@csrf_exempt
@require_POST
def print_queue_done(request, agent_key, job_id):
    """Agent calls this after successfully printing a job."""
    outlet = _outlet_by_key(agent_key)
    if outlet is None:
        return JsonResponse({"error": "Invalid key"}, status=403)

    updated = PrintJob.objects.filter(
        pk=job_id, tenant_id=outlet.tenant_id, outlet=outlet,
        status=PrintJob.PROCESSING,
    ).update(status=PrintJob.DONE, done_at=timezone.now())

    if not updated:
        logger.warning(
            "PrintQueue: done called for job %s but not in PROCESSING state (outlet %d)",
            job_id, outlet.pk,
        )
        return JsonResponse({"error": "Job not found or already done"}, status=404)

    return JsonResponse({"success": True})


@csrf_exempt
@require_POST
def print_queue_failed(request, agent_key, job_id):
    """Agent calls this when a print job fails (printer unreachable etc.)."""
    import json
    outlet = _outlet_by_key(agent_key)
    if outlet is None:
        return JsonResponse({"error": "Invalid key"}, status=403)

    try:
        body = json.loads(request.body)
        msg  = str(body.get("error", ""))[:512]
    except Exception:
        msg = ""

    updated = PrintJob.objects.filter(
        pk=job_id, tenant_id=outlet.tenant_id, outlet=outlet,
        status=PrintJob.PROCESSING,
    ).update(status=PrintJob.FAILED, done_at=timezone.now(), error_msg=msg)

    if not updated:
        logger.warning(
            "PrintQueue: failed called for job %s but not in PROCESSING state (outlet %d)",
            job_id, outlet.pk,
        )

    return JsonResponse({"success": True})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _outlet_by_key(agent_key: str):
    """Return Outlet matching the given UUID key, or None."""
    try:
        import uuid
        return Outlet.objects.get(print_agent_key=uuid.UUID(str(agent_key)))
    except (Outlet.DoesNotExist, ValueError):
        return None


def _build_receipt_b64(order, chars, cut, encoding) -> str:
    """
    Build complete ESC/POS bytes for a receipt and return as a base64 string.
    Base64 avoids PostgreSQL jsonb rejecting raw control characters (ESC \x1B, GS \x1D).
    """
    import base64
    from orders.services.printing_service import PrintingService

    class BytesPrinter:
        def __init__(self):   self.buf = b""
        def text(self, t):
            if isinstance(t, str):
                self.buf += t.encode(encoding, errors="replace")
            else:
                self.buf += bytes(t)
        def set(self, **kw):  pass
        def cut(self, mode="FULL"):
            self.buf += b'\x1d\x56\x00' if mode == "FULL" else b'\x1d\x56\x01'

    svc = PrintingService(chars_per_line=chars, cut_type=cut, encoding=encoding)
    buf = BytesPrinter()
    svc._print_bill_body(buf, order)
    buf.cut(mode="FULL")
    return base64.b64encode(buf.buf).decode("ascii")