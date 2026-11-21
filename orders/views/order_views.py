# orders/views/order_views.py
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.decorators import may, tenant_required, feature_required
from django.urls import reverse
from core.errors import error_response
from orders.exceptions import OrderError
from orders.models import Order, OrderItem
from tablemerge.models import TableMerge

logger = logging.getLogger("pos.orders")


def running_order_page_data(order, user):
    """What static/js/running_order/index.js needs from the server, rendered
    once by running_order.html as {{ page_data|json_script:"page-data" }}:
    how often the order is asked for, what this user may do (asked of the
    views themselves, so a button shows exactly when the server would take
    the tap), and every address the script calls (0 stands in for an id
    the script fills in)."""
    from kitchen.views import serve_item
    from orders.services import approvals
    from orders.views.discount_views import make_item_complimentary
    from orders.views.order_actions import cancel_item, cancel_order, reduce_item
    return {
        "poll_ms": 3000,
        "order_id": order.id,
        "can": {
            "edit": may(user, reduce_item) and may(user, cancel_item),
            "cancel_order": may(user, cancel_order),
            "serve": may(user, serve_item),
            "comp": may(user, make_item_complimentary),
            # A served dish needs a manager (void_service): locked for the rest.
            "edit_served": approvals.approves_own(user),
            # A guest's QR dish: a tick and a cross (approve_items, refuse_item).
            "approve": may(user, approve_items) and may(user, refuse_item),
        },
        "urls": {
            "data": reverse("running-order-data", args=[order.id]),
            "reduce": reverse("reduce-item", args=[0]),
            "cancel": reverse("cancel-item", args=[0]),
            "cancel_order": reverse("cancel-order", args=[0]),
            "serve": reverse("serve-item", args=[0]),
            "comp": reverse("make-complimentary", args=[0]),
            "approve": reverse("approve-items", args=[order.id]),
            "refuse": reverse("refuse-item", args=[0]),
            "bill": reverse("bill-view", args=[order.id]),
            "tables": reverse("table-dashboard"),
        },
    }


@login_required
@tenant_required
@feature_required("running_order")
def running_order_view(request, order_id):
    order = Order.objects.filter(
        id=order_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).first()

    if not order:
        return JsonResponse({"error": "Order not found"}, status=404)

    return render(request, "orders/running_order.html",
                  {"order": order, "page_data": running_order_page_data(order, request.user)})


@login_required
@tenant_required
def running_order_items(request):
    # Shared by two different screens for two different reasons: fine
    # dining's Running Order page (feature "running_order") and QSR/cafe's
    # Token Billing (feature "token_system", which is what actually needs
    # this data -- franchise/cafe tenants don't get "running_order" at
    # all by default). feature_required() only supports requiring ALL of
    # a list of features, not EITHER/OR, so this is checked inline instead
    # -- gating on "running_order" alone left Token Billing's own item list
    # silently 403ing (rendered as an unparseable HTML 403 page, not JSON,
    # since this URL doesn't start with /api/) for any cafe/franchise
    # tenant without a manual feature override.
    from core.features import has_feature
    tenant = getattr(request.user, "tenant", None)
    if not request.user.is_superuser and not (
        has_feature(tenant, "running_order") or has_feature(tenant, "token_system")
    ):
        return JsonResponse(
            {"error": "This feature is not available for your account type."}, status=403
        )
    try:
        table_id = request.GET.get("table")
        order_id = request.GET.get("order")

        tenant = request.user.tenant
        outlet = request.user.outlet

        order = None

        # Filtering/ordering the items HERE, as part of the Prefetch
        # queryset, is what actually makes it get cached -- doing
        # order.items.exclude(...).order_by(...) later (as this used to)
        # builds a brand new, uncached queryset on the related manager
        # every time it's touched, silently throwing away the
        # prefetch_related above it and firing a fresh query per item for
        # menu_item and modifiers (classic N+1). This endpoint is polled
        # every 5s by every staff member with Token Billing open, so that
        # N+1 was real, recurring load on an already memory-constrained
        # server -- not just theoretical.
        from django.db.models import Prefetch
        _items_prefetch = Prefetch(
            "items",
            queryset=(
                OrderItem.objects
                .exclude(status="voided")
                .order_by("id")
                .select_related("menu_item")
                .prefetch_related("modifiers")
            ),
        )

        if order_id:
            try:
                order_id = int(order_id)
                order = (
                    Order.objects
                    .filter(id=order_id, tenant=tenant, outlet=outlet)
                    .prefetch_related(_items_prefetch)
                    .first()
                )
            except (ValueError, TypeError):
                pass
        elif table_id:
            try:
                table_id = int(table_id)
                # Resolve table merge
                merge = (
                    TableMerge.objects
                    .filter(
                        tenant=tenant, outlet=outlet,
                        is_active=True, tables__id=table_id
                    )
                    .select_related("primary_table")
                    .first()
                )
                if merge:
                    table_id = merge.primary_table.id

                order = (
                    Order.objects
                    .filter(
                        tenant=tenant, outlet=outlet,
                        table_id=table_id,
                        status__in=["open", "billing"]
                    )
                    .prefetch_related(_items_prefetch)
                    .order_by("-created_at")
                    .first()
                )
            except (ValueError, TypeError):
                pass

        if not order:
            return JsonResponse({"items": [], "order_id": None, "order_status": None})

        items = []
        for i in order.items.all():  # already filtered/ordered/prefetched via _items_prefetch above
            item_name = i.menu_item.name if i.menu_item else "Unknown Item"
            items.append({
                "id": i.id,
                "name": item_name,
                "quantity": i.quantity,
                "status": i.status,
                "price": float(i.price),
                "total": float(i.total_price),
                "modifiers": [m.name for m in i.modifiers.all()]
            })

        token = getattr(order, "token", None)
        remaining = max(0, float(order.amount_due()))

        return JsonResponse({
            "items": items,
            "order_id": order.id,
            "order_status": order.status,
            "order_status_display": order.get_status_display(),
            "grand_total": float(order.grand_total or 0),
            "parcel_on": float(order.parcel_surcharge or 0) > 0,
            "parcel_amount": float(order.parcel_surcharge or 0),
            # Everything the Token Billing "Current Order" header/action area
            # needs to switch to a different order WITHOUT a full page reload
            # (see tokens/templates/tokens/token_billing.html::selectToken) --
            # a full navigation there used to re-render the whole menu grid,
            # popularity sort, and payment config just to show a different
            # order's items.
            "token_display": token.display_number if token else None,
            "remaining": remaining,
        })

    except Exception as e:
        logger.error("running_order_items error: %s", e)
        return JsonResponse({"items": [], "order_id": None})


@login_required
@tenant_required
@feature_required("running_order")
def running_order_data(request, order_id):
    from django.db.models import Prefetch
    from django.utils import timezone
    from orders.services.void_service import kitchen_stock_hint

    order = (
        Order.objects
        .select_related("table")
        .prefetch_related(Prefetch(
            "items",
            queryset=OrderItem.objects.select_related("menu_item", "kot__station").prefetch_related("modifiers"),
        ))
        .filter(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        .first()
    )

    if not order:
        return JsonResponse({"error": "Order not found"}, status=404)

    is_manager = request.user.is_superuser or request.user.role in ("owner", "manager")
    now = timezone.now()
    items = []
    for i in order.items.all():
        items.append({
            "id": i.id,
            "name": i.menu_item.name,
            "quantity": i.quantity,
            "status": i.status,
            "void_reason": i.void_reason or "",
            "not_accepted": i.not_accepted,
            "is_complimentary": i.is_complimentary,
            "modifiers": [m.name for m in i.modifiers.all()],
            **kitchen_stock_hint(i, is_manager, now),
        })

    return JsonResponse({
        "subtotal": float(order.subtotal),
        "gst": float(order.gst_total),
        "total": float(order.grand_total),
        "items": items
    })


def _item_ids(request):
    """The dish ids in a request's body, {"item_ids": [3, 4]}: a list of
    whole numbers, or None when the body names none (meaning all of them).
    A body that is not JSON names none, as before 21 Nov 2026."""
    import json
    if request.content_type != "application/json":
        return None
    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, UnicodeDecodeError):
        raise OrderError("The request could not be read.")
    ids = data.get("item_ids") if isinstance(data, dict) else None
    if ids is None:
        return None
    if not isinstance(ids, list) or not ids or not all(type(i) is int for i in ids):
        raise OrderError("item_ids must be a list of dish ids.")
    return ids


def _approved(result):
    sent = bool(result["kots"])
    return JsonResponse({
        "success": True,
        "count": len(result["approved"]),
        "approved": result["approved"],
        "sent_to_kitchen": sent,
        "kots": result["kots"],
        "waiting": result["waiting"],
    })


@login_required
@tenant_required
@feature_required("qr_menu")
@require_POST
def approve_items(request, order_id):
    """
    Accept a guest's QR dishes on one order: every one still waiting, or
    only those in {"item_ids": [...]}. A dine-in order's accepted dishes go
    to the kitchen now; a token order's at payment
    (orders/services/guest_items.py, the one rule for every screen).
    """
    from orders.services.guest_items import approve_guest_items

    # The order is found (or a 404) before the body is read.
    if not Order.objects.filter(id=order_id, tenant=request.user.tenant, outlet=request.user.outlet).exists():
        return JsonResponse({"error": "Order not found"}, status=404)
    try:
        return _approved(approve_guest_items(request.user, order_id, _item_ids(request)))
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except OrderError as e:
        return error_response(e, 400)
    except Exception:
        logger.exception("approve_items error")
        return JsonResponse({"error": "Could not approve the items. Please try again."}, status=400)


@login_required
@tenant_required
@feature_required("qr_menu")
@require_POST
def approve_item(request, item_id):
    """
    Accept one guest dish. The same rule as approve_items: on a dine-in
    order it goes to the kitchen now, on a token order at payment. (Before
    21 Nov 2026 this one never sent a ticket, so a dine-in dish approved on
    its own never reached the kitchen.)
    """
    from orders.services.guest_items import approve_guest_items

    order_id = (
        OrderItem.objects
        .filter(id=item_id, order__tenant=request.user.tenant, order__outlet=request.user.outlet)
        .values_list("order_id", flat=True)
        .first()
    )
    if order_id is None:
        return JsonResponse({"error": "Item not found"}, status=404)
    try:
        return _approved(approve_guest_items(request.user, order_id, [item_id]))
    except Order.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except OrderError as e:
        return error_response(e, 400)
    except Exception:
        logger.exception("approve_item error")
        return JsonResponse({"error": "Could not approve the item. Please try again."}, status=400)


@login_required
@tenant_required
@feature_required("qr_menu")
@require_POST
def refuse_item(request, item_id):
    """
    Not accept one guest dish still waiting for approval. Body:
    {"reason": "Not available"}. The dish is cancelled, the kitchen never
    saw it, and the guest's phone says "Not accepted". No manager PIN,
    unless the bill is already printed (then the usual PIN prompt).
    """
    from orders.services import approvals
    from orders.services.guest_items import refuse_guest_item
    from orders.views.order_actions import _json_body, _needs_approval, _reason

    if not OrderItem.objects.filter(id=item_id, order__tenant=request.user.tenant,
                                    order__outlet=request.user.outlet).exists():
        return JsonResponse({"error": "Item not found"}, status=404)
    try:
        approver = approvals.read_approval(request, request.user.outlet)
        item = refuse_guest_item(request.user, item_id, _reason(_json_body(request), "Not accepted"),
                                 approver=approver)
        new_total = Order.objects.get(id=item.order_id).grand_total
        return JsonResponse({"success": True, "new_total": float(new_total)})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except (approvals.ApprovalNeeded, approvals.ApprovalRefused) as e:
        return _needs_approval(request, e)
    except OrderError as e:
        return error_response(e, 400)
    except Exception:
        logger.exception("refuse_item error")
        return JsonResponse({"error": "Could not take the dish off. Please try again."}, status=400)
