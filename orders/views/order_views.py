# orders/views/order_views.py
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from django.db import transaction
from core.decorators import tenant_required, feature_required
from orders.models import Order, OrderItem
from tablemerge.models import TableMerge
from orders.services.event_service import log_event

logger = logging.getLogger("pos.orders")


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

    return render(request, "orders/running_order.html", {"order": order})


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

        from django.db.models import Sum
        token = getattr(order, "token", None)
        total_paid = (
            order.payments.exclude(method="refund").aggregate(total=Sum("amount"))["total"]
            or 0
        )
        remaining = max(0, float(order.grand_total or 0) - float(total_paid))

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
    order = (
        Order.objects
        .select_related("table")
        .prefetch_related("items__menu_item", "items__modifiers")
        .filter(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        .first()
    )

    if not order:
        return JsonResponse({"error": "Order not found"}, status=404)

    items = []
    for i in order.items.all():
        items.append({
            "id": i.id,
            "name": i.menu_item.name,
            "quantity": i.quantity,
            "status": i.status,
            "modifiers": [m.name for m in i.modifiers.all()]
        })

    return JsonResponse({
        "subtotal": float(order.subtotal),
        "gst": float(order.gst_total),
        "total": float(order.grand_total),
        "items": items
    })


@login_required
@tenant_required
@feature_required("qr_menu")
@require_POST
def approve_items(request, order_id):
    """Waiters approve items added via QR code."""
    try:
        tenant = request.user.tenant
        outlet = request.user.outlet
        
        with transaction.atomic():
            items = OrderItem.objects.filter(
                order_id=order_id,
                order__tenant=tenant,
                order__outlet=outlet,
                status="review"
            ).select_for_update()
            
            if not items.exists():
                return JsonResponse({"error": "No items found for approval"}, status=404)
            
            count = items.count()
            items.update(status="pending")
            
            order = Order.objects.get(id=order_id)
            
            # Trigger KOT creation for the newly approved items
            try:
                from kitchen.services.kot_service import create_kot
                create_kot(request.user, order)
            except Exception as kot_err:
                logger.error("KOT Creation failed during approval: %s", kot_err)
                # We don't fail the whole request, but we log it
            
            log_event(order, "status_changed", request.user, {"action": "items_approved", "count": count})
            
            logger.info("User %s approved %s items for order #%s", request.user.username, count, order_id)
            
        return JsonResponse({"success": True, "count": count})
    except Exception as e:
        logger.error("approve_items error: %s", e)
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@feature_required("qr_menu")
@require_POST
def approve_item(request, item_id):
    """
    Singular sibling of approve_items — lets staff approve one QR-guest
    item at a time instead of all-or-nothing, with cancel_item (existing,
    already "review"-safe — see void_service.void_order_item) as the
    per-item counterpart for rejecting instead of approving.

    Deliberately does NOT create a KOT (unlike approve_items, which does).
    approve_items is fine-dining's "waiter approves a QR add-on" action --
    dine-in customers eat before they pay, so kitchen prep starting the
    moment an item is approved is correct there. This one is used on
    Token Billing (QSR/cafe counter orders), which is the opposite model:
    pay first, then the kitchen fires -- see pay_order's _auto_kot, which
    now fires for every token order regardless of whether the outlet also
    runs a kitchen display. Approving here just accepts the item onto the
    bill; the KOT gets created once at payment, not once per approval.
    """
    try:
        with transaction.atomic():
            item = (
                OrderItem.objects
                .select_for_update()
                .select_related("order")
                .get(
                    id=item_id,
                    order__tenant=request.user.tenant,
                    order__outlet=request.user.outlet,
                )
            )
            if item.status != "review":
                return JsonResponse({"error": "Item is not awaiting approval"}, status=400)

            item.status = "pending"
            item.save(update_fields=["status"])

            order = item.order
            log_event(order, "status_changed", request.user, {"action": "item_approved", "item_id": item.id})
            logger.info("User %s approved item #%s", request.user.username, item_id)

        return JsonResponse({"success": True})
    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except Exception as e:
        logger.error("approve_item error: %s", e)
        return JsonResponse({"error": str(e)}, status=400)
