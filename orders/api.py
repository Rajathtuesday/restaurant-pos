import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Prefetch, Q
from django.core.serializers.json import DjangoJSONEncoder
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction, IntegrityError
from django.conf import settings
from django.utils import timezone

from core.decorators import tenant_required
from notifications.models import Notification
from kitchen.models import KitchenMessage
from waiter.models import WaiterCall

@login_required
@tenant_required
def notification_api(request):
    """
    Unified endpoint for real-time notifications (Waiter Calls + Kitchen Messages).
    Used by the 8s global poller in base.html and standalone templates.
    """
    outlet = request.user.outlet
    tenant = request.user.tenant

    # 1. Active Waiter Calls (not resolved)
    waiter_calls_qs = WaiterCall.objects.filter(
        tenant=tenant, outlet=outlet, is_resolved=False
    ).select_related('table').order_by('-created_at')
    wc_count = waiter_calls_qs.count()
    waiter_calls = waiter_calls_qs[:20]   # cap items returned; count is real total

    # 2. Active Kitchen Messages (not acknowledged)
    # Waiters only see messages for orders they created (their own tables).
    # Managers / owners / cashiers see all messages for the outlet.
    kitchen_msgs_qs = KitchenMessage.objects.filter(
        tenant=tenant, outlet=outlet, is_resolved=False
    ).select_related('order', 'order__table').order_by('-created_at')
    if request.user.role == 'waiter':
        kitchen_msgs_qs = kitchen_msgs_qs.filter(order__created_by=request.user)
    km_count = kitchen_msgs_qs.count()
    kitchen_msgs = kitchen_msgs_qs[:20]

    # 3. Unread System Notifications — capped at 50.
    # Without a limit, low-stock alerts accumulate and this query returns
    # thousands of rows on every 8-second poll across all open browser tabs.
    unread_system = Notification.objects.filter(
        tenant=tenant, outlet=outlet, is_read=False
    ).order_by('-created_at')[:50]

    # 4. QR orders awaiting staff approval (guest-placed items land as
    # status="review" — see orders/services/order_service.py). Distinct order
    # count, not item count, so one order with 5 review items shows as "1".
    qr_orders_qs = (
        Order.objects.filter(
            tenant=tenant, outlet=outlet, items__status="review"
        )
        .distinct()
        .select_related("table")
        .order_by("-created_at")
    )
    qr_count = qr_orders_qs.count()
    qr_orders = qr_orders_qs[:20]

    return JsonResponse({
        "waiter_calls": {
            "count": wc_count,
            "items": [{"id": c.id, "table": c.table.name} for c in waiter_calls]
        },
        "kitchen_messages": {
            "count": km_count,
            "items": [{
                "id": m.id,
                "table": m.order.table.name if m.order.table else "Takeaway",
                "message": m.message
            } for m in kitchen_msgs]
        },
        "qr_orders": {
            "count": qr_count,
            "items": [{
                "id": o.id,
                "table": o.table.name if o.table else "Takeaway",
            } for o in qr_orders]
        },
        "notifications": [
            {"id": n.id, "message": n.message} for n in unread_system
        ]
    })

from orders.models import Table, Order, OrderItem, Payment
from tenants.models import Tenant, Outlet
from setup.models import AggregatorConfig
from orders.services.tax_service import tax_snapshot_for
from menu.models import MenuItem

logger = logging.getLogger("pos.api")

def is_ip_allowed(request):
    """
    Validates if the incoming request is from an allowed aggregator IP.
    HIGH-4: Implementation of IP allowlist.
    """
    if settings.DEBUG:
        return True

    # The same client IP rate limits and login lockouts use (core/utils.py),
    # which believes a forwarded header only from a machine trusted to set it.
    # Entries in AGGREGATOR_IP_ALLOWLIST may be addresses or CIDR ranges.
    from core.utils import get_client_ip, ip_in
    return ip_in(get_client_ip(request), settings.AGGREGATOR_IP_ALLOWLIST)

@login_required
@tenant_required
def api_tables(request):
    """
    Real-time tables state checking.
    """
    tables = Table.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).order_by('name')

    data = []
    for table in tables:
        data.append({
            "id": table.id,
            "name": table.name,
            "state": table.state,
            "is_active": table.is_active
        })

    return JsonResponse({"success": True, "data": data}, encoder=DjangoJSONEncoder)


@login_required
@tenant_required
def api_active_orders(request):
    """
    Returns full order/ticket data for the active outlet avoiding race-condition deadlocks.
    """
    orders = Order.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        status__in=["open", "billing"]
    ).prefetch_related(
        Prefetch("items", queryset=OrderItem.objects.select_related("menu_item"))
    ).select_related("table")

    data = []
    for order in orders:
        items_data = []
        for item in order.items.all():
            items_data.append({
                "id": item.id,
                "name": item.menu_item.name if item.menu_item else "Unknown (Deleted)",
                "quantity": item.quantity,
                "status": item.status,
                "price": item.price,
                "total_price": item.total_price,
                "is_complimentary": item.is_complimentary,
                "void_reason": item.void_reason
            })
            
        data.append({
            "id": order.id,
            "order_number": order.order_number,
            "bill_number": order.bill_number,
            "table_id": order.table_id if order.table else None,
            "table_name": order.table.name if order.table else "Walk-in",
            "status": order.status,
            "subtotal": order.subtotal,
            "gst_total": order.gst_total,
            "discount_total": order.discount_total,
            "grand_total": order.grand_total,
            "created_at": str(order.created_at),
            "items": items_data
        })

    return JsonResponse({"success": True, "data": data}, encoder=DjangoJSONEncoder)



def _as_id(value):
    """A positive database id from JSON or a query string, else None."""
    if isinstance(value, bool):
        return None
    text = str(value).strip() if value is not None else ""
    return int(text) if text.isdigit() and int(text) > 0 else None


def _ingested(order, duplicate=False):
    return JsonResponse({
        "success": True,
        "duplicate": duplicate,
        "order_id": order.id,
        "order_number": order.order_number,
        "bill_number": order.bill_number,
    })


_WEBHOOK_SECRET_FIELDS = {"zomato": "zomato_webhook_secret", "swiggy": "swiggy_webhook_secret"}


@csrf_exempt
@require_POST
def api_ingest_order(request):
    """
    Webhook for Zomato / Swiggy orders (or a partner relaying them).

    Before anything is read from the database beyond the outlet's webhook
    settings, and before anything is written: the caller's IP is on
    AGGREGATOR_IP_ALLOWLIST, the body is a JSON object, and the request is
    signed with that outlet's secret for the source and is fresh
    (orders/services/aggregator_webhook.py). An unknown tenant, outlet or
    secret and a bad signature all get the same 401, so the endpoint can't
    be used to probe which IDs exist, and none of those lookups can raise.

    A platform retries a delivery until it gets a 2xx, so an order ID seen
    before answers 200 with the order already made, never a second order,
    and every order must carry its ID.
    """
    from core.utils import get_client_ip
    from orders.services import aggregator_webhook
    from orders.services.cart_limits import MAX_CART_LINES, line_quantity

    if not is_ip_allowed(request):
        logger.warning("Rejected ingest attempt from unauthorized IP: %s", get_client_ip(request))
        return JsonResponse({"error": "Unauthorized IP"}, status=403)

    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # Explicit allowlist, not an if/else fallback: an unrecognized source
    # (a typo, "web", "uber_eats") must not validate against some other
    # platform's secret.
    source = request.GET.get("source") or data.get("source")
    secret_field = _WEBHOOK_SECRET_FIELDS.get(source) if isinstance(source, str) else None
    if not secret_field:
        return JsonResponse({"error": "Unknown aggregator source"}, status=401)

    tenant_pk = _as_id(request.GET.get("tenant_id") or data.get("tenant_id"))
    outlet_pk = _as_id(request.GET.get("outlet_id") or data.get("outlet_id"))
    config = None
    if tenant_pk and outlet_pk:
        config = (
            AggregatorConfig.objects.select_related("tenant", "outlet")
            .filter(tenant_id=tenant_pk, outlet_id=outlet_pk, outlet__tenant_id=tenant_pk)
            .first()
        )
    secret = getattr(config, secret_field, "") if config else ""
    if not aggregator_webhook.verify(
        secret,
        request.headers.get(aggregator_webhook.TIMESTAMP_HEADER),
        request.headers.get(aggregator_webhook.SIGNATURE_HEADER),
        request.body,
    ):
        return JsonResponse({"error": "Invalid or expired signature"}, status=401)
    tenant, outlet = config.tenant, config.outlet

    # Every order carries the platform's own ID: it is what makes a retried
    # delivery the same order (the unique constraint ignores a blank one).
    aggregator_id = data.get("aggregator_order_id")
    if isinstance(aggregator_id, int) and not isinstance(aggregator_id, bool):
        aggregator_id = str(aggregator_id)
    if not isinstance(aggregator_id, str) or not aggregator_id.strip():
        return JsonResponse({"error": "aggregator_order_id is required"}, status=400)
    aggregator_id = aggregator_id.strip()
    if len(aggregator_id) > Order._meta.get_field("aggregator_order_id").max_length:
        return JsonResponse({"error": "aggregator_order_id is too long"}, status=400)

    existing = Order.objects.filter(outlet=outlet, aggregator_order_id=aggregator_id).first()
    if existing:
        return _ingested(existing, duplicate=True)

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return JsonResponse({"error": "items must be a non-empty list"}, status=400)
    if len(items) > MAX_CART_LINES:
        return JsonResponse({"error": f"An order can have at most {MAX_CART_LINES} lines"}, status=400)

    # Validate every line BEFORE any writes happen. A `return` from inside
    # `transaction.atomic()` does NOT roll back (only an exception does), so
    # bailing out mid-loop used to commit a half-built, status="paid" order.
    lines = []
    menu_items_by_id = {}
    for line in items:
        if not isinstance(line, dict):
            return JsonResponse({"error": "Each item must be an object"}, status=400)
        menu_item_id = _as_id(line.get("menu_item_id"))
        try:
            quantity = line_quantity(line.get("quantity", 1))
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)
        if menu_item_id not in menu_items_by_id:
            menu_item = (
                MenuItem.objects.select_related("vat_class")
                .filter(id=menu_item_id, tenant=tenant, outlet=outlet).first()
                if menu_item_id else None
            )
            if not menu_item:
                return JsonResponse(
                    {"error": f"Menu item id={line.get('menu_item_id')} not found or not available at this outlet"},
                    status=422,
                )
            menu_items_by_id[menu_item_id] = menu_item
        lines.append((menu_items_by_id[menu_item_id], quantity))

    # Liquor may not be sold through an aggregator or the web, so an
    # order with any liquor line is refused whole, before anything is
    # written. (Liquor lines exist only with the liquor_vat feature.)
    liquor = sorted({
        menu_item.name for menu_item in menu_items_by_id.values()
        if tax_snapshot_for(menu_item, tenant)["tax_kind"] == "vat"
    })
    if liquor:
        return JsonResponse(
            {"error": "Liquor can't be ordered through an aggregator: " + ", ".join(liquor)},
            status=422,
        )

    try:
        with transaction.atomic():
            order = Order.objects.create(
                tenant=tenant,
                outlet=outlet,
                source=source,
                aggregator_order_id=aggregator_id,
                status="paid",  # Aggregator orders usually come pre-paid
            )

            # When auto-KOT is on we route items through create_kot below,
            # which only picks up status="pending" items and transitions
            # them to "sent" itself (while deducting inventory + printing).
            # Creating them as "sent" up front would make create_kot find
            # nothing to do. When auto-KOT is off, mark them "sent" directly.
            auto_kot = config.auto_accept_orders
            initial_item_status = "pending" if auto_kot else "sent"

            for menu_item, quantity in lines:
                OrderItem.objects.create(
                    order=order,
                    menu_item=menu_item,
                    quantity=quantity,
                    price=menu_item.price,
                    **tax_snapshot_for(menu_item, tenant),
                    total_price=menu_item.price * quantity,
                    status=initial_item_status,
                )

            order.recalculate_totals()

            # Aggregator orders arrive pre-paid: the platform has already
            # collected the money. One payment row at the grand total, with
            # the platform as the method, so revenue shows in daily sales.
            Payment.objects.create(
                order=order,
                method=source,
                amount=order.grand_total,
                reference=aggregator_id,
                created_by=None,
            )

            # Auto KOT: create the kitchen ticket, deduct inventory, and
            # queue printing. create_kot() takes user=None here (no logged-in
            # staff member for a webhook order) and derives tenant/outlet
            # from the order itself.
            if auto_kot:
                from kitchen.services.kot_service import create_kot
                create_kot(None, order)

            # Assign online token if tenant uses token_system
            from core.features import has_feature
            if has_feature(tenant, "token_system"):
                from tokens.views import assign_online_token
                from core.utils import get_business_date
                business_date = get_business_date(timezone.now(), outlet)
                tok = assign_online_token(order, outlet, tenant, business_date)
                logger.info(
                    "Online token %s assigned to order %s (source=%s)",
                    tok.display_number, order.id, source,
                )
    except IntegrityError:
        # Two deliveries of the same order at once: both passed the check
        # above before either committed, and the unique constraint on
        # (outlet, aggregator_order_id) stopped the second. It is the same
        # order, so the answer is the one already made.
        existing = Order.objects.filter(outlet=outlet, aggregator_order_id=aggregator_id).first()
        if existing:
            return _ingested(existing, duplicate=True)
        logger.exception("Failed to ingest order via API")
        return JsonResponse({"error": "Internal Server Error"}, status=500)
    except Exception:
        logger.exception("Failed to ingest order via API")
        return JsonResponse({"error": "Internal Server Error"}, status=500)

    return _ingested(order)
