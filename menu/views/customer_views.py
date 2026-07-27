"""Customer-facing views: QR menu, digital self-order menu, waiter call."""
import json
import logging
from django.http import JsonResponse, Http404
from django.shortcuts import render, get_object_or_404

from menu.models import MenuCategory
from orders.models import Table
from waiter.models import WaiterCall

logger = logging.getLogger("pos.menu")


def _build_modifier_data(categories):
    """Return a JSON string: {item_id: [{group}]} for items that have modifier groups."""
    data = {}
    for cat in categories:
        for item in cat.items.all():
            groups = []
            for img in item.modifier_groups.all():
                mg = img.modifier_group
                mods = [
                    {"id": m.id, "name": m.name, "price": float(m.price)}
                    for m in mg.modifiers.all() if m.is_active
                ]
                if mods:
                    groups.append({
                        "id": mg.id, "name": mg.name,
                        "is_required": mg.is_required,
                        "max_select": mg.max_select,
                        "modifiers": mods,
                    })
            if groups:
                data[str(item.id)] = groups
    return json.dumps(data)


def menu_view(request, qr_token):
    """QR-scan entry point. Renders the digital self-order menu.

    qr_token resolves against Table.qr_token first (dine-in, per-table QR).
    If nothing matches, it's checked against Outlet.qr_token -- the
    outlet-wide "Counter / Walk-in" QR for QSR/cafe outlets with no seating
    to hang a per-table QR on. That path renders the same menu with no
    table, so an order placed here lands as table=None (a walk-in order).
    """
    from core.features import has_feature
    from tenants.models import Outlet

    table = Table.objects.filter(qr_token=qr_token).first()
    if table:
        tenant, outlet = table.tenant, table.outlet
    else:
        outlet = get_object_or_404(Outlet, qr_token=qr_token)
        tenant = outlet.tenant

    if not has_feature(tenant, "qr_menu"):
        raise Http404

    categories = list(
        MenuCategory.objects
        .filter(tenant=tenant, outlet=outlet, is_active=True)
        .prefetch_related("items", "items__modifier_groups__modifier_group__modifiers")
    )
    return render(request, "menu/digital_menu.html", {
        "table":               table,
        "categories":          categories,
        "tenant":              tenant,
        "outlet":              outlet,
        "item_modifier_data":  _build_modifier_data(categories),
    })


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


def order_status(request, order_id):
    """
    Public, read-only status poll for a guest who just placed a QR order.
    No login required — a guest has no account. Deliberately returns only
    non-sensitive fields (item names/quantities/status, a plain-English stage)
    and NOTHING financial (no prices, totals, or payment info), since the
    order_id alone is not treated as a secret.
    """
    from orders.models import Order

    order = get_object_or_404(Order, id=order_id)

    items = list(order.items.exclude(status="voided").select_related("menu_item"))

    # Collapse per-item statuses into one plain-English stage for the customer.
    # Mirrors the staff-facing priority order in orders/views/table_views.py
    # (needs_approval > ordering > preparing > ready > served) but worded for
    # a guest rather than staff.
    statuses = {i.status for i in items}
    if not items:
        stage, label = "placed", "Order received"
    elif "review" in statuses:
        stage, label = "waiting_confirmation", "Waiting for the restaurant to confirm your order"
    elif statuses & {"sent", "preparing"}:
        stage, label = "preparing", "Your food is being prepared"
    elif "ready" in statuses:
        stage, label = "ready", "Your food is ready!"
    elif statuses and statuses == {"served"}:
        stage, label = "served", "Enjoy your meal!"
    elif "pending" in statuses:
        stage, label = "confirmed", "Order confirmed — sending to the kitchen"
    else:
        stage, label = "placed", "Order received"

    token = getattr(order, "token", None)

    return JsonResponse({
        "order_id": order.id,
        "table": order.table.name if order.table else "Takeaway",
        # QSR only -- lets the guest's own page show "Your order #12 is
        # ready!" matching the public display board (tokens/views.py::display_board).
        "token_display": token.display_number if token else None,
        "token_ready": bool(token and token.ready_at and not token.collected_at),
        "token_collected": bool(token and token.collected_at),
        # Order-level lifecycle status (open/billing/paid/closed/cancelled) —
        # NOT item stage. Lets the guest's page know when to stop polling and
        # stop offering "add more items" (billing_views.create_order already
        # refuses to merge into anything that isn't open/billing server-side;
        # this just lets the frontend match that instead of guessing).
        "order_status": order.status,
        "can_add_more": order.status in ("open", "billing"),
        "stage": stage,
        "label": label,
        "items": [
            {
                "name": i.menu_item.name if i.menu_item else "Item",
                "quantity": i.quantity,
                "status": i.status,
            }
            for i in items
        ],
    })


def digital_menu(request):
    """Customer-facing self-order menu with category tabs and cart."""
    from core.features import has_feature

    # Deliberately table_token only. A `?table=<id>` fallback used to exist
    # here, resolving a table by its plain integer id with no auth check —
    # table ids are small sequential integers, so that path let anyone
    # enumerate them and have the page hand back that table's real,
    # secret qr_token, completely bypassing the "must physically scan the
    # QR code" guarantee for the whole tenant. Confirmed dead code before
    # removing it: no template or view anywhere links to
    # /menu/digital-menu/?table=, staff's own "preview menu" link
    # (reports/dashboard.html) uses no query params at all.
    table_token = request.GET.get("table_token")
    table = None
    if table_token:
        table = Table.objects.filter(qr_token=table_token).first()

    if table:
        tenant, outlet = table.tenant, table.outlet
    elif request.user.is_authenticated:
        tenant, outlet = request.user.tenant, request.user.outlet
    else:
        raise Http404("No valid table token provided.")

    if not has_feature(tenant, "qr_menu"):
        raise Http404

    categories = list(
        MenuCategory.objects.filter(tenant=tenant, outlet=outlet, is_active=True)
        .prefetch_related("items", "items__modifier_groups__modifier_group__modifiers")
    )

    return render(request, "menu/digital_menu.html", {
        "categories": categories, "table": table, "tenant": tenant, "outlet": outlet,
        "item_modifier_data": _build_modifier_data(categories),
    })
