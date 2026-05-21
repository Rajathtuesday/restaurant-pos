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
    """
    Print bill + KOTs after payment.

    Always prints synchronously — the web request blocks for ~1-2s while the
    printer responds, which is acceptable at payment time. Celery is intentionally
    NOT used here because:
      • A Celery worker may not be running (local dev, small setups)
      • Redis queues the task silently even with no worker → silent failure
      • KOTs already print via kot_service sync fallback when order goes to kitchen

    Mode detection (mirrors tasks.print_bill_task):
      strip_mode=True    → QSR: compact token receipt + KOTs (tenant has no kitchen_display)
      cashier_strip=True → Hotel/one-printer: full bill + KOTs connected strip
      neither            → Fine dining with per-station printers: bill only
    """
    from orders.models import KOTBatch
    from orders.services.printing_service import PrintingService
    from setup.services.station_service import get_default_station
    from core.features import has_feature

    try:
        order = Order.objects.prefetch_related("items", "payments").get(
            id=order_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
        station = get_default_station(request.user)
        if not station or not station.printer_ip:
            return JsonResponse(
                {"error": "No printer configured. Set printer IP in Setup → Kitchen Stations."},
                status=400,
            )

        kots = list(
            KOTBatch.objects
            .filter(order=order)
            .prefetch_related("items__menu_item", "items__modifiers")
            .select_related("station")
            .order_by("kot_number")
        )

        printer = PrintingService(
            printer_type="network",
            host=station.printer_ip,
            port=station.printer_port,
            chars_per_line=station.chars_per_line,
            cut_type=station.cut_type,
            encoding=station.printer_encoding,
        )

        # ── Mode detection ──────────────────────────────────────────
        strip_mode = not has_feature(order.tenant, "kitchen_display")

        any_station_has_printer = any(
            kot.station and kot.station.printer_ip and not kot.station.is_default
            for kot in kots
        )

        if strip_mode:
            if any_station_has_printer:
                success = printer.print_token_receipt(order)
            else:
                success = printer.print_bill_with_kots(order, kots, strip_mode=True)
        else:
            if any_station_has_printer:
                success = printer.print_bill(order)
            else:
                # One cashier printer: bill → partial → KOT1 → partial → KOT2 → FULL CUT
                success = printer.print_bill_with_kots(order, kots, cashier_strip=True)

        if success:
            logger.info("Bill + %d KOT(s) printed for order #%s", len(kots), order.id)
            return JsonResponse({"success": True, "message": "Bill and KOTs printed"})

        return JsonResponse({"error": "Printer connected but print failed. Check paper and cable."}, status=500)

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        logger.exception("Error printing bill for order %s", order_id)
        return JsonResponse({"error": str(e)}, status=500)


# -------------------------------------------------
# PRINT SPLIT BILL (Counter Billing Mode)
# -------------------------------------------------

@login_required
@require_POST
@tenant_required
@role_required("manager", "cashier", "owner")
def print_split_bill(request, order_id):
    """
    Prints summary slip + one slip per menu category.
    Used when outlet.split_bill_by_category = True.
    Customer takes each category slip to the relevant counter.
    """
    from orders.services.printing_service import PrintingService
    from setup.services.station_service import get_default_station
    try:
        order = Order.objects.prefetch_related(
            "items__menu_item__category", "payments", "token"
        ).get(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)

        station = get_default_station(request.user)
        if not station or not station.printer_ip:
            return JsonResponse(
                {"error": "No printer configured. Set printer IP in Kitchen Stations."},
                status=400,
            )

        printer = PrintingService(
            printer_type="network",
            host=station.printer_ip,
            port=station.printer_port,
            chars_per_line=station.chars_per_line,
            cut_type=station.cut_type,
            encoding=station.printer_encoding,
        )
        success = printer.print_split_by_category(order)

        if success:
            logger.info("Split bill printed for order #%s", order_id)
            return JsonResponse({"success": True, "message": "Split bill printed"})

        return JsonResponse({"error": "Printer connection failed."}, status=400)

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        logger.exception("Error printing split bill for order %s", order_id)
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
# PRINTER STATUS  - GET /orders/printer-status/
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
            f"{station} is not responding (KOT #{kot}) - "
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

    # Exclude refund rows (negative amounts) - including them inflates 'remaining'.
    # This mirrors the correct calculation already used in bill_view and pay_order.
    remaining = order.grand_total - sum(p.amount for p in order.payments.exclude(method="refund"))

    html_string = render_to_string("orders/bill.html", {"order": order, "request": request, "remaining": remaining})
    pdf_file = weasyprint.HTML(string=html_string, base_url=request.build_absolute_uri('/')).write_pdf()

    response = HttpResponse(pdf_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="bill_{order.id}.pdf"'
    return response


# -------------------------------------------------
# THERMAL RECEIPT — browser-based printing
# Opens in a popup, auto-calls window.print(), closes after printing.
# Works with any printer that has a Windows/Mac driver installed.
# This is the cloud-only printing solution — no local agent required.
# -------------------------------------------------

@login_required
@tenant_required
def thermal_receipt_view(request, order_id):
    from django.shortcuts import get_object_or_404, render
    from django.db.models import Sum
    from orders.models import KOTBatch
    from setup.services.station_service import get_default_station
    from core.features import has_feature

    order = get_object_or_404(
        Order.objects.prefetch_related("items__menu_item", "items__modifiers", "payments"),
        id=order_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet,
    )

    kots = []
    token = None
    if not has_feature(order.tenant, "kitchen_display"):
        # QSR strip mode — include KOTs in the receipt
        kots = list(
            KOTBatch.objects
            .filter(order=order)
            .prefetch_related("items__menu_item", "items__modifiers")
            .select_related("station")
            .order_by("kot_number")
        )
        # Token number for QSR
        try:
            if hasattr(order, "token") and order.token:
                token = order.token.display_number
        except Exception:
            pass

    # Payment and change
    payment    = order.payments.order_by("-paid_at").first()
    total_paid = order.payments.exclude(method="refund").aggregate(t=Sum("amount"))["t"] or 0
    change_due = max(0, total_paid - order.grand_total)

    # Paper dimensions from default station
    station       = get_default_station(request.user)
    paper_width   = station.paper_width_mm if station else 80
    chars         = 32 if paper_width == 58 else 48
    font_size     = 10 if paper_width == 58 else 11
    big_font      = 12 if paper_width == 58 else 13
    small_font    = 9  if paper_width == 58 else 10
    token_font    = 20 if paper_width == 58 else 24

    items = order.items.exclude(status="voided").select_related("menu_item__category")

    # Counter Billing Mode — group items by category for split browser print
    split_mode = getattr(order.outlet, "split_bill_by_category", False)
    category_groups = []
    if split_mode:
        groups: dict = {}
        _UNCAT = "uncategorised"
        for item in items.order_by("menu_item__category__name"):
            cat = item.menu_item.category if item.menu_item else None
            key = cat.id if cat else _UNCAT
            if key not in groups:
                groups[key] = {
                    "category": cat,
                    "cat_name": cat.name if cat else "General",
                    "items": [],
                    "total": 0,
                }
            groups[key]["items"].append(item)
            groups[key]["total"] += item.total_price
        category_groups = list(groups.values())

    return render(request, "orders/thermal_receipt.html", {
        "order":           order,
        "items":           items,
        "kots":            kots,
        "token":           token,
        "payment":         payment,
        "change_due":      change_due,
        "paper_width":     paper_width,
        "font_size":       font_size,
        "big_font":        big_font,
        "small_font":      small_font,
        "token_font":      token_font,
        "split_mode":      split_mode,
        "category_groups": category_groups,
    })
