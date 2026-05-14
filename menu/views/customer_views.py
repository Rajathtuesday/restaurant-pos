"""Customer-facing views: QR menu, digital self-order menu, waiter call."""
import logging
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404

from menu.models import MenuCategory
from orders.models import Table, WaiterCall

logger = logging.getLogger("pos.menu")


def menu_view(request, qr_token):
    """QR-scan entry point. Renders the legacy table-locked menu."""
    from core.features import has_feature

    table = get_object_or_404(Table, qr_token=qr_token)
    if not has_feature(table.tenant, "qr_menu"):
        raise Http404

    categories = (
        MenuCategory.objects
        .filter(tenant=table.tenant, outlet=table.outlet, is_active=True)
        .prefetch_related("items", "items__modifier_groups__modifier_group__modifiers")
    )
    return render(request, "menu/menu.html", {"table": table, "categories": categories})


def call_waiter(request, qr_token):
    """Customer taps 'Call Waiter' from the QR menu. Rate-limited to one call/60s."""
    from django.utils import timezone
    from datetime import timedelta
    from core.features import has_feature

    table = get_object_or_404(Table, qr_token=qr_token)
    if not has_feature(table.tenant, "waiter_call"):
        return JsonResponse({"error": "Waiter call is not available."}, status=403)

    recent = WaiterCall.objects.filter(
        table=table, is_resolved=False,
        created_at__gte=timezone.now() - timedelta(seconds=60)
    ).exists()
    if recent:
        return JsonResponse({"error": "A waiter has already been called. Please wait."}, status=429)

    WaiterCall.objects.create(tenant=table.tenant, outlet=table.outlet, table=table)
    return JsonResponse({"success": True})


def digital_menu(request):
    """Customer-facing self-order menu with category tabs and cart."""
    from core.features import has_feature

    table_token = request.GET.get("table_token")
    table_id    = request.GET.get("table")
    table = None
    if table_token:
        table = Table.objects.filter(qr_token=table_token).first()
    elif table_id:
        table = Table.objects.filter(id=table_id).first()

    if table:
        tenant, outlet = table.tenant, table.outlet
    elif request.user.is_authenticated:
        tenant, outlet = request.user.tenant, request.user.outlet
    else:
        raise Http404("No valid table token provided.")

    if not has_feature(tenant, "qr_menu"):
        raise Http404

    categories = MenuCategory.objects.filter(
        tenant=tenant, outlet=outlet, is_active=True
    ).prefetch_related("items")

    return render(request, "menu/digital_menu.html", {
        "categories": categories, "table": table, "tenant": tenant, "outlet": outlet,
    })
