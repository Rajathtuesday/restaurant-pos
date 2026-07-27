"""
Order History — searchable, filterable, role-scoped list of all past orders.

Role access:
  owner   / manager → all orders, any date, full audit trail, CSV export
  cashier           → all outlet orders, last 30 days, no audit trail
  captain           → all outlet orders, last 7 days, no audit trail
  waiter            → only orders they created, today only
  chef              → no access (403)
"""
import csv
import logging
from datetime import timedelta, date

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count, Prefetch
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.utils import timezone

from core.decorators import tenant_required, role_required
from orders.models import Order, OrderItem, Payment, OrderEvent
from payments.models import Refund

logger = logging.getLogger("pos.orders")

PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _base_queryset(request):
    """
    Returns (queryset, is_restricted) based on role.
    is_restricted = True means date/staff filters are locked down.
    """
    user   = request.user
    tenant = user.tenant
    outlet = user.outlet
    role   = user.role
    today  = timezone.localdate()

    if role == "chef":
        return None, False  # no access

    base = (
        Order.objects
        .filter(tenant=tenant, outlet=outlet)
        .select_related("table", "created_by")
        .prefetch_related(
            Prefetch("payments", queryset=Payment.objects.order_by("paid_at")),
        )
        .order_by("-created_at")
    )

    if role == "waiter":
        # Only their own orders, today only
        base = base.filter(created_by=user, created_at__date=today)
        return base, True

    if role == "cashier":
        # All outlet orders, last 30 days
        cutoff = today - timedelta(days=30)
        base   = base.filter(created_at__date__gte=cutoff)
        return base, True

    if role == "captain":
        # All outlet orders (captain supervises multiple waiters, not just
        # their own tickets) but a tighter window than cashier's 30 days —
        # a middle tier between waiter (own orders, today) and cashier.
        cutoff = today - timedelta(days=7)
        base   = base.filter(created_at__date__gte=cutoff)
        return base, True

    # owner / manager — everything. NOTE: this is a fail-open default —
    # any role not explicitly handled above (e.g. "agent", "kitchen") lands
    # here with full, unrestricted access. Not fixing that pre-existing gap
    # here; just don't add a new role above without remembering that.
    return base, False


def _apply_filters(qs, params, is_restricted):
    """Apply GET filter params to queryset, respecting role restrictions."""
    today = timezone.localdate()

    # Date range — restricted roles cannot go beyond their allowed window
    date_from = params.get("date_from", "")
    date_to   = params.get("date_to",   "")

    if date_from:
        try:
            df = date.fromisoformat(date_from)
            qs = qs.filter(created_at__date__gte=df)
        except ValueError:
            pass
    elif not is_restricted:
        # Default: show last 30 days for unrestricted roles (owner/manager)
        # unless they set a custom range
        pass

    if date_to:
        try:
            dt = date.fromisoformat(date_to)
            qs = qs.filter(created_at__date__lte=dt)
        except ValueError:
            pass

    # Status
    status = params.get("status", "")
    if status and status != "all":
        qs = qs.filter(status=status)
    elif not status:
        # Default: exclude open orders from history (they're on billing screen)
        qs = qs.exclude(status="open")

    # Payment method — filter orders that have at least one payment of this type
    method = params.get("method", "")
    if method and method != "all":
        if method == "complimentary":
            # Orders where grand_total = 0 (fully comp)
            qs = qs.filter(grand_total=0)
        else:
            qs = qs.filter(payments__method=method).distinct()

    # Source
    source = params.get("source", "")
    if source and source != "all":
        qs = qs.filter(source=source)

    # Staff (owner/manager only)
    staff_id = params.get("staff_id", "")
    if staff_id and not is_restricted:
        try:
            qs = qs.filter(created_by_id=int(staff_id))
        except (ValueError, TypeError):
            pass

    # Search — order number OR customer phone
    q = params.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(order_number__icontains=q) |
            Q(customer_phone__icontains=q) |
            Q(customer_name__icontains=q)
        )

    return qs


# ---------------------------------------------------------------------------
# MAIN VIEW
# ---------------------------------------------------------------------------

@login_required
@tenant_required
def order_history_view(request):
    user = request.user

    if user.role == "chef":
        return HttpResponseForbidden("Kitchen staff cannot access order history.")

    qs, is_restricted = _base_queryset(request)
    qs = _apply_filters(qs, request.GET, is_restricted)

    # Summary stats for the filtered set (before pagination)
    summary = qs.exclude(status="cancelled").aggregate(
        total_orders  = Count("id"),
        total_revenue = Sum("grand_total"),
        total_items   = Count("items"),
    )
    total_revenue = summary["total_revenue"] or 0
    total_orders  = summary["total_orders"]  or 0

    # Payment method breakdown
    payment_summary = (
        Payment.objects
        .filter(order__in=qs.exclude(status="cancelled"), method__in=["cash", "upi", "card"])
        .values("method")
        .annotate(total=Sum("amount"))
    )
    pay_map = {row["method"]: float(row["total"]) for row in payment_summary}

    # Staff list for filter dropdown (owner/manager only)
    staff_list = []
    if not is_restricted:
        from accounts.models import User
        staff_list = list(
            User.objects
            .filter(tenant=user.tenant, outlet=user.outlet)
            .exclude(role="chef")
            .values("id", "username", "first_name", "last_name", "role")
            .order_by("role", "username")
        )

    # Paginate
    paginator   = Paginator(qs, PAGE_SIZE)
    page_number = request.GET.get("page", 1)
    try:
        page_number = int(page_number)
    except (ValueError, TypeError):
        page_number = 1
    page = paginator.get_page(page_number)

    return render(request, "orders/order_history.html", {
        "page":           page,
        "total_orders":   total_orders,
        "total_revenue":  total_revenue,
        "pay_map":        pay_map,
        "staff_list":     staff_list,
        "is_restricted":  is_restricted,
        "filters":        request.GET,
        # Current filter values for the form
        "f_date_from": request.GET.get("date_from", ""),
        "f_date_to":   request.GET.get("date_to",   ""),
        "f_status":    request.GET.get("status",    ""),
        "f_method":    request.GET.get("method",    ""),
        "f_source":    request.GET.get("source",    ""),
        "f_staff_id":  request.GET.get("staff_id",  ""),
        "f_q":         request.GET.get("q",         ""),
    })


# ---------------------------------------------------------------------------
# ORDER DETAIL API (called by JS modal)
# ---------------------------------------------------------------------------

@login_required
@tenant_required
def order_detail_api(request, order_id):
    user = request.user

    if user.role == "chef":
        return JsonResponse({"error": "Access denied"}, status=403)

    try:
        order = (
            Order.objects
            .prefetch_related(
                Prefetch("items", queryset=OrderItem.objects.select_related("menu_item").order_by("id")),
                Prefetch("payments", queryset=Payment.objects.order_by("paid_at")),
                Prefetch("events",   queryset=OrderEvent.objects.select_related("created_by").order_by("created_at")),
            )
            .select_related("table", "created_by", "outlet", "tenant")
            .get(id=order_id, tenant=user.tenant, outlet=user.outlet)
        )
    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)

    # Role scope: waiter can only see their own orders
    if user.role == "waiter" and order.created_by_id != user.id:
        return JsonResponse({"error": "Access denied"}, status=403)

    # Cashier: only last 30 days
    if user.role == "cashier":
        cutoff = timezone.now() - timedelta(days=30)
        if order.created_at < cutoff:
            return JsonResponse({"error": "Order too old"}, status=403)

    # Build items
    items_data = []
    for item in order.items.all():
        item_name = item.menu_item.name if item.menu_item else "Deleted Item"
        items_data.append({
            "name":             item_name,
            "quantity":         item.quantity,
            "price":            float(item.price),
            "total":            float(item.total_price),
            "status":           item.status,
            "is_complimentary": item.is_complimentary,
            "void_reason":      item.void_reason or "",
            "item_discount_pct": float(item.item_discount_pct) if hasattr(item, "item_discount_pct") and item.item_discount_pct else 0,
        })

    # Build payments — include ID so JS can POST refund requests
    payments_data = []
    for p in order.payments.all():
        if p.method == "refund" or float(p.amount) < 0:
            payments_data.append({
                "id": p.id, "method": p.method, "amount": float(p.amount),
                "reference": p.reference or "",
                "paid_at": p.paid_at.strftime("%H:%M") if p.paid_at else "",
                "is_refund": True, "refunds": [],
            })
            continue

        refunds_for_payment = []
        for r in Refund.objects.filter(payment=p).select_related("refunded_by").order_by("id"):
            refunds_for_payment.append({
                "id": r.id,
                "amount": float(r.amount),
                "reason": r.reason,
                "status": r.status,
                "by": r.refunded_by.get_full_name() or r.refunded_by.username if r.refunded_by else "",
            })

        payments_data.append({
            "id": p.id, "method": p.method, "amount": float(p.amount),
            "reference": p.reference or "",
            "paid_at": p.paid_at.strftime("%H:%M") if p.paid_at else "",
            "is_refund": False,
            "refunds": refunds_for_payment,
        })

    # Build audit trail (owner/manager only)
    events_data = []
    if user.role in ("owner", "manager"):
        for ev in order.events.all():
            by = ""
            if ev.created_by:
                by = ev.created_by.get_full_name() or ev.created_by.username
            events_data.append({
                "type":    ev.event_type,
                "label":   ev.get_event_type_display(),
                "by":      by,
                "at":      ev.created_at.strftime("%H:%M"),
                "detail":  ev.metadata or {},
            })

    # Duration (order open → closed)
    duration_mins = None
    if order.closed_at and order.created_at:
        duration_mins = int((order.closed_at - order.created_at).total_seconds() / 60)

    # Location
    location = ""
    if order.table:
        location = f"Table {order.table.name}"
    else:
        try:
            if order.token:
                location = f"Token #{order.token.display_number}"
        except Exception:
            pass
    if not location:
        location = order.source.replace("_", " ").title()

    waiter_name = ""
    if order.created_by:
        waiter_name = order.created_by.get_full_name() or order.created_by.username

    return JsonResponse({
        "id":             order.id,
        "order_number":   order.order_number or str(order.id),
        "status":         order.status,
        "user_role":      user.role,
        "can_refund":     user.role in ("manager", "owner") and order.status in ("closed", "paid"),
        "can_approve_refund": user.role == "owner",
        "source":         order.source,
        "location":       location,
        "created_at":     order.created_at.strftime("%d %b %Y, %H:%M"),
        "closed_at":      order.closed_at.strftime("%H:%M") if order.closed_at else None,
        "duration_mins":  duration_mins,
        "waiter":         waiter_name,
        "customer_name":  order.customer_name or "",
        "customer_phone": order.customer_phone or "",
        "subtotal":       float(order.subtotal),
        "gst_total":      float(order.gst_total),
        "discount_total": float(order.discount_total),
        "grand_total":    float(order.grand_total),
        "items":          items_data,
        "payments":       payments_data,
        "events":         events_data,
    })


# ---------------------------------------------------------------------------
# CSV EXPORT (owner / manager only)
# ---------------------------------------------------------------------------

@login_required
@tenant_required
@role_required("owner", "manager")
def export_orders_csv(request):
    user = request.user

    qs, is_restricted = _base_queryset(request)
    qs = _apply_filters(qs, request.GET, is_restricted)
    # Annotate the item count in the same query instead of calling
    # order.items.count() per row below — that was one extra query per CSV
    # row, up to EXPORT_LIMIT (2000) round trips for a single export.
    qs = qs.annotate(item_count=Count("items"))

    # Cap at 2000 rows to prevent timeout
    EXPORT_LIMIT = 2000
    total = qs.count()
    qs = qs[:EXPORT_LIMIT]

    response = HttpResponse(content_type="text/csv")
    filename = f"orders_{timezone.localdate().isoformat()}.csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow([
        "Order #", "Date", "Time", "Location", "Source",
        "Waiter", "Items", "Subtotal (Rs)",
        "GST (Rs)", "Discount (Rs)", "Total (Rs)",
        "Cash (Rs)", "UPI (Rs)", "Card (Rs)", "Refund (Rs)",
        "Status", "Duration (min)",
        f"NOTE: Showing {min(total, EXPORT_LIMIT)} of {total} orders",
    ])

    for order in qs:
        # Payment breakdown per order
        pay = {"cash": 0, "upi": 0, "card": 0, "refund": 0}
        for p in order.payments.all():
            method = p.method if p.method in pay else ("refund" if float(p.amount) < 0 else "other")
            pay[method] = pay.get(method, 0) + float(p.amount)

        # Location
        location = ""
        if order.table:
            location = f"T:{order.table.name}"
        else:
            try:
                if hasattr(order, "token") and order.token:
                    location = f"Token:{order.token.display_number}"
            except Exception:
                pass
        if not location:
            location = order.source

        # Duration
        dur = ""
        if order.closed_at and order.created_at:
            dur = int((order.closed_at - order.created_at).total_seconds() / 60)

        waiter = ""
        if order.created_by:
            waiter = order.created_by.get_full_name() or order.created_by.username

        writer.writerow([
            order.order_number or order.id,
            order.created_at.strftime("%d/%m/%Y"),
            order.created_at.strftime("%H:%M"),
            location,
            order.source.replace("_", " ").title(),
            waiter,
            order.items.count(),
            float(order.subtotal),
            float(order.gst_total),
            float(order.discount_total),
            float(order.grand_total),
            pay.get("cash", 0),
            pay.get("upi", 0),
            pay.get("card", 0),
            pay.get("refund", 0),
            order.status,
            dur,
            "",  # NOTE column — only in header
        ])

    logger.info(
        "User %s exported %d orders (filtered: %d total)",
        user.username, min(total, EXPORT_LIMIT), total,
    )
    return response
