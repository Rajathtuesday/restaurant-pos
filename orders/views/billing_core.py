# orders/views/billing_core.py
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Sum, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from core.decorators import tenant_required
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table
from tablemerge.models import TableMerge
from orders.services.order_lock_service import lock_order
from setup.models import PaymentConfig
from offers.services import page_offers

logger = logging.getLogger("pos.orders")


# -------------------------------------------------
# BILLING PAGE
# -------------------------------------------------

# The addresses the billing screen's script calls, by the name it knows
# them by. Those that take an id are reversed with 0 standing in for it
# (static/js/billing/urls.js puts the real one in).
BILLING_URLS = {
    "create_order": ("create-order", False),
    "running_order_items": ("running-order-items", False),
    "printer_status": ("printer-status", False),
    "print_queue_add": ("print-queue-add", False),
    "send_to_kitchen": ("send-to-kitchen", True),
    "toggle_parcel": ("toggle-parcel", True),
    "generate_bill": ("generate-bill", True),
    "bill_view": ("bill-view", True),
    "cancel_order": ("cancel-order", True),
    "pay_order": ("pay-order", True),
    "qz_receipt_data": ("qz-receipt-data", True),
    "thermal_receipt": ("thermal-receipt", True),
    "approve_item": ("approve-item", True),
    "cancel_item": ("cancel-item", True),
    "item_modifiers": ("menu_item_modifiers", True),
}


def billing_page_data(outlet, mode, station_printer_ip="", order_id=None):
    """What static/js/billing/index.js needs from the server, rendered once
    by the template as {{ page_data|json_script:"page-data" }}.

    mode: "dine_in" (tables, dispatch to the kitchen), "counter" (pay and
    print the slip in one go) or "token" (the token counter)."""
    from core.features import has_feature
    return {
        "mode": mode,
        # Without the feature the item-modifiers address answers 403, so
        # the screen is told not to ask on every dish tap.
        "modifiers": has_feature(outlet.tenant, "modifiers"),
        "urls": {key: reverse(name, args=[0] if takes_id else None)
                 for key, (name, takes_id) in BILLING_URLS.items()},
        "outlet": {
            "id": outlet.id,
            "inclusive": bool(outlet.gst_inclusive),
            "vatInclusive": bool(outlet.vat_inclusive),
            "composition": bool(outlet.is_composition_scheme),
            "gstRegistered": bool(outlet.is_gst_registered),
            "parcelGstRate": float(outlet.parcel_gst_rate or 0),
            "parcelCharge": float(outlet.parcel_charge_amount or 0),
            # The outlet's rate is charged on every item, or once per order.
            "parcelPerItem": bool(outlet.parcel_charge_per_item),
        },
        # An order through one of these apps carries no GST of its own,
        # because the app pays it (orders/services/tax_engine.py).
        "operator_sources": list(Order.OPERATOR_SOURCES),
        "station_printer_ip": station_printer_ip or "",
        "order_id": order_id,
    }


@login_required
@tenant_required
def billing_view(request):
    """
    Renders the main POS billing interface for staff.
    Handles table merging logic and enforces optimistic locking to prevent
    concurrent edits by multiple staff members on the same order.
    """
    table_id = request.GET.get("table")
    order = None

    if table_id:
        merge = (
            TableMerge.objects
            .filter(tenant=request.user.tenant, outlet=request.user.outlet,
                    is_active=True, tables__id=table_id)
            .select_related("primary_table").first()
        )
        if merge and str(table_id) != str(merge.primary_table.id):
            table_id = merge.primary_table.id

    if table_id:
        order = (
            Order.objects
            .filter(tenant=request.user.tenant, outlet=request.user.outlet,
                    table_id=table_id, status__in=["open", "billing"])
            .select_related("table", "lock").first()
        )

    if order:
        locked, locked_user = lock_order(order, request.user)
        if not locked:
            return render(request, "orders/order_locked.html", {"locked_by": locked_user, "order": order})

    categories = list(
        MenuCategory.objects
        .filter(tenant=request.user.tenant, outlet=request.user.outlet, is_active=True)
        .prefetch_related(Prefetch("items", queryset=MenuItem.objects.filter(is_available=True)))
    )

    tables = Table.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet, is_active=True
    ).order_by("name")

    from core.features import has_feature
    from setup.models import KitchenStation
    tenant = request.user.tenant
    outlet = request.user.outlet

    has_kds        = has_feature(tenant, "kitchen_display")
    has_kt_printer = KitchenStation.objects.filter(
        tenant=tenant, outlet=outlet, is_active=True, is_default=False
    ).exclude(printer_ip__isnull=True).exclude(printer_ip="").exists()

    # auto_kot_mode: no KDS screen AND no separate kitchen printer
    # → KOT is created at payment time and prints together with the bill
    # → customer carries the combined slip to the food counter
    auto_kot_mode = not has_kds and not has_kt_printer

    default_station = KitchenStation.objects.filter(
        tenant=tenant, outlet=outlet, is_active=True
    ).exclude(printer_ip__isnull=True).exclude(printer_ip="").first()

    # Each dish's tax as the bill will charge it (GST, or VAT for liquor), for the cart
    from orders.services.tax_service import sale_tax_map
    dish_tax = sale_tax_map((item for category in categories for item in category.items.all()), tenant)

    return render(request, "orders/billing.html", {
        "categories":      categories,
        "dish_tax":        dish_tax,
        "page_offers":     page_offers(request.user.tenant, request.user.outlet),
        "tables":          tables,
        "order":           order,
        "selected_table":  table_id,
        "auto_kot_mode":   auto_kot_mode,
        "outlet":          request.user.outlet,
        "page_data": billing_page_data(
            request.user.outlet, "counter" if auto_kot_mode else "dine_in",
            station_printer_ip=default_station.printer_ip if default_station else "",
        ),
    })


# -------------------------------------------------
# BILL VIEW
# -------------------------------------------------

@never_cache
@login_required
@tenant_required
def bill_view(request, order_id):
    """
    Renders the billing detail page for a specific order.
    Calculates remaining balance dynamically based on existing payments
    and handles table state transitions.
    """
    try:

        order = Order.objects.get(
            id=order_id, tenant=request.user.tenant, outlet=request.user.outlet
        )

        # Only while the order is still actually awaiting payment -- once
        # it's paid/closed/cancelled, this page reloads on its own right
        # after payment (to trigger printing) and must not undo the
        # "cleaning" state pay_order already correctly set a moment earlier.
        if order.table and order.status in ("open", "billing"):
            order.table.state = "billing"
            order.table.save(update_fields=["state"])

        # Get payment configuration
        config, _ = PaymentConfig.for_outlet(request.user.outlet, request.user.tenant)

        # Calculate remaining balance
        total_paid = order.payments.exclude(method="refund").aggregate(total=Sum("amount"))["total"] or Decimal("0")
        remaining = order.grand_total - total_paid

        # Available promos
        from promos.models import Promo
        promos = Promo.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        ).filter(Q(outlet=request.user.outlet) | Q(outlet__isnull=True))

        valid_promos = [p for p in promos if p.is_live_for(request.user.outlet)]

        # A promo below its minimum stays on the bill and takes nothing off
        # (tax_engine rule 1); say so, and what it needs.
        promo_paused_note = ""
        if order.promo_name and order.status not in ("paid", "closed") and order.promo_paused:
            from orders.services.bill_layout import rupees
            promo_paused_note = (f"{order.promo_name} needs a bill of {rupees(order.promo_min_order)} "
                                 f"(now {rupees(order.menu_value())}). It applies again once the bill gets there.")

        from core.features import has_feature
        from setup.services.station_service import get_default_station
        tenant = request.user.tenant
        direct_billing_mode = has_feature(tenant, "direct_billing_mode")
        razorpay_feature_enabled = has_feature(tenant, "razorpay_gateway")
        is_qsr        = tenant.tenant_type in ("franchise", "cafe")
        # Worded by the GST scheme the bill was totalled under, not by the
        # outlet's settings today, so an issued bill reads the same forever.
        is_composition  = order.is_bill_of_supply
        is_unregistered = order.gst_scheme == "unregistered"
        gst_inclusive   = getattr(order.outlet, "gst_inclusive", False) and order.is_tax_invoice

        station = get_default_station(request.user)
        paper_width_mm = station.paper_width_mm if station else 80

        # Auto-print: after payment reload, pass print=1 so template opens receipt
        auto_print = request.GET.get("print") == "1"

        from orders.services.bill_layout import bill_layout
        return render(request, "orders/bill.html", {
            "order": order,
            "layout": bill_layout(order),
            "config": config,
            "remaining": remaining,
            "total_paid": total_paid,
            "promos": valid_promos,
            "promo_paused_note": promo_paused_note,
            "direct_billing_mode": direct_billing_mode,
            "razorpay_feature_enabled": razorpay_feature_enabled,
            "is_qsr":        is_qsr,
            "gst_inclusive":  gst_inclusive,
            "is_composition": is_composition,
            "is_unregistered": is_unregistered,
            "paper_width_mm":  paper_width_mm,
            "auto_print":      auto_print,
            "default_station": station,
        })
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
