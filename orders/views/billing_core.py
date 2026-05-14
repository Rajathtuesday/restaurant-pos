# orders/views/billing_core.py
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch, Sum, Q
from django.http import JsonResponse
from django.shortcuts import render

from core.decorators import tenant_required
from menu.models import MenuCategory, MenuItem
from orders.models import Order, Table, TableMerge, Payment
from orders.services.order_lock_service import lock_order
from setup.models import PaymentConfig

logger = logging.getLogger("pos.orders")


# -------------------------------------------------
# BILLING PAGE
# -------------------------------------------------

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

    categories = (
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

    return render(request, "orders/billing.html", {
        "categories": categories, "tables": tables,
        "order": order, "selected_table": table_id,
        "auto_kot_mode": auto_kot_mode,
    })


# -------------------------------------------------
# BILL VIEW
# -------------------------------------------------

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

        if order.table:
            order.table.state = "billing"
            order.table.save(update_fields=["state"])

        # Get payment configuration
        config, _ = PaymentConfig.objects.get_or_create(
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        # Calculate remaining balance
        total_paid = order.payments.exclude(method="refund").aggregate(total=Sum("amount"))["total"] or Decimal("0")
        remaining = order.grand_total - total_paid

        # Available promos
        from orders.models import Promo
        promos = Promo.objects.filter(
            tenant=request.user.tenant,
            is_active=True
        ).filter(Q(outlet=request.user.outlet) | Q(outlet__isnull=True))

        valid_promos = [p for p in promos if p.is_currently_valid]

        from core.features import has_feature
        from setup.services.station_service import get_default_station
        direct_billing_mode = has_feature(request.user.tenant, "direct_billing_mode")

        station = get_default_station(request.user)
        paper_width_mm = station.paper_width_mm if station else 80

        return render(request, "orders/bill.html", {
            "order": order,
            "config": config,
            "remaining": remaining,
            "total_paid": total_paid,
            "promos": valid_promos,
            "direct_billing_mode": direct_billing_mode,
            "paper_width_mm": paper_width_mm,
        })
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
