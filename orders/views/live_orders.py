# orders/views/live_orders.py
"""
Live Orders board: every open order at this outlet, with its dishes, on one
screen. Read-only here; edits go through the existing per-item endpoints
(reduce-item, cancel-item), which do their own tenant/outlet and role checks.
"""
import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from core.decorators import tenant_required, role_required
from core.features import has_feature
from orders.models import Order, OrderItem
from orders.services.void_service import kitchen_stock_hint

logger = logging.getLogger("pos.orders")

VIEW_ROLES = ("owner", "manager", "cashier", "captain", "waiter")
EDIT_ROLES = ("owner", "manager", "cashier", "captain")
MANAGER_ROLES = ("owner", "manager")
ONLINE_SOURCES = ("zomato", "swiggy", "uber_eats", "web")


def _feature_denied(request):
    # Dine-in tenants get "running_order", cafe/franchise tenants get
    # "token_system"; either one is enough for this board.
    if request.user.is_superuser:
        return None
    tenant = request.user.tenant
    if has_feature(tenant, "running_order") or has_feature(tenant, "token_system"):
        return None
    return JsonResponse({"error": "This feature is not available for your account type."}, status=403)


@login_required
@tenant_required
@role_required(*VIEW_ROLES)
def live_orders_view(request):
    denied = _feature_denied(request)
    if denied:
        return denied
    return render(request, "orders/live_orders.html")


def _order_label(order, token):
    if order.table_id:
        return order.table.name, "table"
    if token:
        return f"Token {token.display_number}", "online" if token.is_online else "token"
    if order.source in ONLINE_SOURCES:
        return order.get_source_display(), "online"
    return order.get_source_display(), "takeaway"


@login_required
@tenant_required
@role_required(*VIEW_ROLES)
def live_orders_data(request):
    denied = _feature_denied(request)
    if denied:
        return denied

    user = request.user
    role = getattr(user, "role", None)
    can_edit = user.is_superuser or role in EDIT_ROLES
    is_manager = user.is_superuser or role in MANAGER_ROLES
    has_running_page = user.is_superuser or has_feature(user.tenant, "running_order")

    # Every relation the loop below touches is loaded here, in a fixed number
    # of queries, so the poll costs the same with 2 open orders or 50. The
    # item filter/ordering lives INSIDE the Prefetch on purpose: filtering
    # order.items later would discard the prefetch and query once per order.
    orders = (
        Order.objects
        .filter(tenant=user.tenant, outlet=user.outlet, status__in=["open", "billing"])
        .select_related("table", "token", "created_by")
        .prefetch_related(Prefetch(
            "items",
            queryset=(
                OrderItem.objects
                .exclude(status="voided")
                .select_related("menu_item", "kot")
                .prefetch_related("modifiers")
                .order_by("id")
            ),
        ))
        .order_by("created_at")
    )

    now = timezone.now()
    summary = {"open_orders": 0, "in_kitchen": 0, "ready": 0, "awaiting_bill": 0, "review": 0}
    data = []

    for order in orders:
        token = getattr(order, "token", None)
        label, kind = _order_label(order, token)

        items = []
        counts = {"in_kitchen": 0, "ready": 0, "review": 0}
        for i in order.items.all():
            if i.status in ("sent", "preparing"):
                counts["in_kitchen"] += 1
            elif i.status == "ready":
                counts["ready"] += 1
            elif i.status == "review":
                counts["review"] += 1
            served_lock = i.status == "served" and not is_manager
            items.append({
                "id": i.id,
                "name": i.menu_item.name if i.menu_item else "Unknown Item",
                "quantity": i.quantity,
                "status": i.status,
                "modifiers": [m.name for m in i.modifiers.all()],
                "notes": i.notes or "",
                "is_complimentary": i.is_complimentary,
                "can_reduce": can_edit and not served_lock,
                "needs_manager": can_edit and served_lock,
                **kitchen_stock_hint(i, is_manager, now),
            })

        if order.table_id:
            add_url = f"{reverse('billing-view')}?table={order.table_id}"
        elif token and not token.is_online:
            add_url = reverse("token-bill", args=[order.id])
        else:
            add_url = None

        staff = order.created_by
        data.append({
            "id": order.id,
            "label": label,
            "kind": kind,
            "source": order.source,
            "source_display": order.get_source_display(),
            "status": order.status,
            "age_minutes": max(0, int((now - order.created_at).total_seconds() // 60)),
            "staff": (staff.first_name or staff.username) if staff else "",
            "total": float(order.grand_total or 0),
            "items": items,
            "counts": counts,
            "add_url": add_url,
            "bill_url": reverse("bill-view", args=[order.id]),
            "detail_url": reverse("running-order", args=[order.id]) if has_running_page else None,
        })

        summary["open_orders"] += 1
        summary["in_kitchen"] += counts["in_kitchen"]
        summary["ready"] += counts["ready"]
        summary["review"] += counts["review"]
        if order.status == "billing":
            summary["awaiting_bill"] += 1

    return JsonResponse({"orders": data, "summary": summary, "can_edit": can_edit})
