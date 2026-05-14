# orders/views/print_views.py
import logging

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, role_required
from orders.models import Order

logger = logging.getLogger("pos.orders")


# -------------------------------------------------
# GENERATE BILL
# -------------------------------------------------

@login_required
@tenant_required
@require_POST
def generate_bill(request, order_id):
    """
    Transitions an active order from 'open' to 'billing' status.
    Triggers a final recalculation of all totals before generating the physical/digital receipt.
    """
    order = (
        Order.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet,
                id=order_id, status="open")
        .first()
    )
    if not order:
        return JsonResponse({"error": "No active order found"}, status=404)

    with transaction.atomic():
        order.status = "billing"
        order.save(update_fields=["status"])
        order.recalculate_totals()

    logger.info(f"User {request.user.username} generated bill for order #{order.id}")
    return JsonResponse({
        "success": True,
        "order_id": order.id,
        "grand_total": float(order.grand_total),
        "subtotal": float(order.subtotal),
        "gst_total": float(order.gst_total),
    })


# -------------------------------------------------
# PRINT BILL ACTION
# -------------------------------------------------

@login_required
@tenant_required
@role_required("manager", "cashier", "owner")
def print_bill_action(request, order_id):
    """Queue bill + KOT print via Celery. Returns immediately — printer runs in background."""
    from orders.tasks import print_bill_task
    from setup.services.station_service import get_default_station
    try:
        order = Order.objects.get(
            id=order_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
        station = get_default_station(request.user)
        if not station or not station.printer_ip:
            return JsonResponse({"error": "No printer configured. Set printer IP in Setup → Kitchen Stations."}, status=400)

        try:
            print_bill_task.delay(order.id, station.id)
            return JsonResponse({"success": True, "message": "Print job queued — bill and KOTs printing now"})
        except Exception as celery_exc:
            # Celery / Redis unavailable — fall back to synchronous print
            logger.warning("Celery unavailable for bill print, falling back to sync: %s", celery_exc)
            from orders.models import KOTBatch
            from orders.services.printing_service import PrintingService
            kots = list(KOTBatch.objects.filter(order=order).order_by("kot_number"))
            printer = PrintingService(
                printer_type="network", host=station.printer_ip, port=station.printer_port,
                chars_per_line=station.chars_per_line, cut_type=station.cut_type,
                encoding=station.printer_encoding,
            )
            success = printer.print_bill_with_kots(order, kots)
            if success:
                return JsonResponse({"success": True, "message": "Bill printed (direct)"})
            return JsonResponse({"error": "Printer connected but print failed."}, status=500)

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        logger.exception("Error printing bill for order %s", order_id)
        return JsonResponse({"error": str(e)}, status=500)


# -------------------------------------------------
# PRINT KOT ACTION
# -------------------------------------------------

@login_required
@require_POST
@tenant_required
@role_required("manager", "cashier", "owner", "kitchen")
def print_kot_action(request, kot_id):
    """Re-print a KOT on the station's thermal printer."""
    from orders.services.printing_service import PrintingService
    from orders.models import KOTBatch
    try:
        kot = KOTBatch.objects.select_related("order", "station").get(
            id=kot_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet,
        )
        station = kot.station
        if not station or not station.printer_ip:
            return JsonResponse({"error": "No printer configured for this station."}, status=400)

        printer = PrintingService(printer_type="network", host=station.printer_ip, port=station.printer_port)
        success = printer.print_kot(kot.order, kot)
        if success:
            return JsonResponse({"success": True, "message": f"KOT #{kot.kot_number} sent to printer"})
        return JsonResponse({"error": "Printer connected but print failed."}, status=500)

    except KOTBatch.DoesNotExist:
        return JsonResponse({"error": "KOT not found"}, status=404)
    except Exception as e:
        logger.exception("Error printing KOT %s", kot_id)
        return JsonResponse({"error": str(e)}, status=500)


# -------------------------------------------------
# PRINTER STATUS  — GET /orders/printer-status/
# Polled by the billing page every 20s to surface print failures
# -------------------------------------------------

@login_required
@tenant_required
def printer_status(request):
    from django.core.cache import cache
    error = cache.get(f"printer_err_{request.user.outlet_id}")
    if error:
        station = error.get("station", "Kitchen printer")
        kot = error.get("kot", "?")
        detail = error.get("detail", "unknown error")
        message = (
            f"{station} is not responding (KOT #{kot}) — "
            f"check the cable/network or print to PDF. ({detail})"
        )
        return JsonResponse({"error": True, "message": message})
    return JsonResponse({"error": False})


# -------------------------------------------------
# DOWNLOAD PDF BILL
# -------------------------------------------------

@login_required
@tenant_required
def download_pdf_bill(request, order_id):
    import weasyprint
    from django.template.loader import render_to_string
    from django.http import HttpResponse
    from django.shortcuts import get_object_or_404

    order = get_object_or_404(Order, id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)

    # Exclude refund rows (negative amounts) — including them inflates 'remaining'.
    # This mirrors the correct calculation already used in bill_view and pay_order.
    remaining = order.grand_total - sum(p.amount for p in order.payments.exclude(method="refund"))

    html_string = render_to_string("orders/bill.html", {"order": order, "request": request, "remaining": remaining})
    pdf_file = weasyprint.HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="bill_{order.id}.pdf"'
    return response
