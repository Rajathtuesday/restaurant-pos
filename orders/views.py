# orders/views.py

import json
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db import transaction
from notifications.services.notification_service import create_notification
from django.db.models import Prefetch

from menu.models import MenuCategory, MenuItem

from .models import (
    Order,
    OrderItem,
    OrderLock,
    Table,
    KOTBatch
)

from orders.services.order_service import (
    get_or_create_open_order,
    add_items_to_order,
    update_table_state
)

from orders.services.kot_service import create_kot
from orders.services.payment_service import process_payment


# -------------------------------------------------
# BILLING PAGE
# -------------------------------------------------

from orders.models import Order
from orders.services.order_lock_service import lock_order

@login_required
def billing_view(request):

    table_id = request.GET.get("table")

    order = None

    
    # FIRST: Fetch order
    if table_id:
        order = Order.objects.filter(
            table_id=table_id,
            status__in=["open", "billing"]
        ).first()
    
    # SECOND: Check lock (AFTER fetching)
    if order:
        locked, user = lock_order(order, request.user)
        if not locked:
            return render(request, "orders/order_locked.html", {
                "locked_by": user,
                "order": order
            })

    categories = MenuCategory.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).prefetch_related(
        Prefetch(
            "items",
            queryset=MenuItem.objects.filter(is_available=True)
        )
    )

    tables = Table.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    )

    return render(request,"orders/billing.html",{
        "categories":categories,
        "tables":tables,
        "order":order,
        "selected_table":table_id
    })

# -------------------------------------------------
# CREATE ORDER
# -------------------------------------------------

@login_required
@require_POST
def create_order(request):

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    cart = data.get("cart")
    table_id = data.get("table_id")

    if not cart:
        return JsonResponse({"error": "Cart empty"}, status=400)

    table = None

    if table_id:

        table = Table.objects.filter(
            id=table_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            is_active=True
        ).first()

        if not table:
            return JsonResponse({"error": "Invalid table"}, status=400)

    try:

        with transaction.atomic():

            order = get_or_create_open_order(request.user, table)

            add_items_to_order(request.user, order, cart)

        return JsonResponse({
            "success": True,
            "order_id": order.id
        })

    except Exception as e:

        return JsonResponse({
            "error": str(e)
        }, status=500)

# -------------------------------------------------
# SEND ORDER TO KITCHEN
# -------------------------------------------------
@login_required
@require_POST
def send_to_kitchen(request, order_id):

    try:

        with transaction.atomic():

            order = (
                Order.objects
                .select_for_update()
                .get(
                    id=order_id,
                    tenant=request.user.tenant,
                    outlet=request.user.outlet
                )
            )

            if order.status not in ["open", "billing"]:
                return JsonResponse({"error": "Order not editable"}, status=400)

            kots = create_kot(request.user, order)

            return JsonResponse({
                "success": True,
                "kots": [k.kot_number for k in kots]
            })

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

# -------------------------------------------------
# KITCHEN SCREEN
# -------------------------------------------------

@login_required
def kitchen_view(request):
    return render(request, "orders/kitchen.html")


@login_required
def kitchen_data(request):

    kots = (
        KOTBatch.objects
        .filter(
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet,
            order__status="open"
        )
        .select_related("order", "order__table")
        .prefetch_related("items__menu_item")
        .order_by("created_at")
    )

    data = []

    for kot in kots:

        items = []

        for i in kot.items.exclude(status__in=["served", "voided"]):

            items.append({
                "id": i.id,
                "name": i.menu_item.name,
                "quantity": i.quantity,
                "status": i.status,
                "notes": i.notes or ""
            })

        if not items:
            continue

        data.append({
            "id": kot.id,
            "kot_number": kot.kot_number,
            "table": kot.order.table.name if kot.order.table else "Takeaway",
            "created_at": kot.created_at.isoformat(),
            "items": items
        })

    return JsonResponse({"kots": data})

# -------------------------------------------------
# KITCHEN ACTIONS
# -------------------------------------------------

@login_required
@require_POST
def start_preparing(request, item_id):

    try:

        item = OrderItem.objects.get(
            id=item_id,
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet
        )

        if item.status != "sent":
            return JsonResponse({"error": "Invalid state"}, status=400)

        item.status = "preparing"
        item.save(update_fields=["status"])

        return JsonResponse({"success": True})

    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)

# -------------------------------------------------
# MARK READY
# -------------------------------------------------


@login_required
@require_POST
def mark_ready(request, item_id):

    try:

        with transaction.atomic():

            item = (
                OrderItem.objects
                .select_related("order")
                .select_for_update()
                .get(
                    id=item_id,
                    order__tenant=request.user.tenant,
                    order__outlet=request.user.outlet
                )
            )

            if item.status != "preparing":
                return JsonResponse({"error": "Invalid state"}, status=400)

            item.status = "ready"
            item.save(update_fields=["status"])

            update_table_state(item.order)

            table_name = "Takeaway"

            if item.order.table:
                table_name = item.order.table.name if item.order.table else "Takeaway"

            create_notification(
                item.order.tenant,
                item.order.outlet,
                "order_ready",
                f"Order ready for {table_name}"
            )

        return JsonResponse({"success": True})

    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)

# -------------------------------------------------
# WAITER SERVE
# -------------------------------------------------

@login_required
@require_POST
def serve_item(request, item_id):

    try:

        item = OrderItem.objects.get(
            id=item_id,
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet
        )

        if item.status != "ready":
            return JsonResponse({"error": "Item not ready"}, status=400)

        item.status = "served"
        item.save(update_fields=["status"])

        update_table_state(item.order)

        return JsonResponse({"success": True})

    except OrderItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)


# -------------------------------------------------
# BILL
# -------------------------------------------------

@login_required
def bill_view(request, order_id):

    try:

        order = Order.objects.get(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        if order.table:
            order.table.state = "billing"
            order.table.save(update_fields=["state"])

        order.recalculate_totals()

        return render(
            request,
            "orders/bill.html",
            {"order": order}
        )

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)


# -------------------------------------------------
# PAYMENT
# -------------------------------------------------

@login_required
@require_POST
def pay_order(request, order_id):

    try:

        data = json.loads(request.body)

        method = data.get("method")

        if method not in ["cash", "upi", "card"]:
            return JsonResponse({"error": "Invalid payment method"}, status=400)

        with transaction.atomic():

            order = (
                Order.objects
                .select_for_update()
                .get(
                    id=order_id,
                    tenant=request.user.tenant,
                    outlet=request.user.outlet
                )
            )

            if order.status not in ["billing", "open"]:
                return JsonResponse({"error": "Order not payable"}, status=400)

            amount = order.grand_total

            process_payment(order, method, amount)

        return JsonResponse({"success": True})

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)

# -------------------------------------------------
# TABLE DASHBOARD
# -------------------------------------------------

@login_required
def table_dashboard(request):
    return render(request, "orders/tables.html")

@login_required
def tables_data(request):

    tenant = request.user.tenant
    outlet = request.user.outlet

    tables = (
        Table.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            is_active=True
        )
        .order_by("name")
    )

    open_orders = (
        Order.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            status="open"
        )
        .select_related("table")
        .prefetch_related("items")
    )

    # Map table_id → order
    orders_map = {o.table_id: o for o in open_orders}

    data = []

    now = timezone.now()

    for table in tables:

        order = orders_map.get(table.id)

        status = table.state
        cooking_items = 0
        elapsed_minutes = 0

        if order:

            cooking_items = sum(
                1 for i in order.items.all()
                if i.status in ["sent", "preparing"]
            )

            elapsed_minutes = int(
                (now - order.created_at).total_seconds() / 60
            )

        if not order and table.state != "cleaning":
            status = "free"

        data.append({
            "id": table.id,
            "name": table.name,
            "status": status,
            "order_id": order.id if order else None,
            "cooking_items": cooking_items,
            "elapsed": elapsed_minutes
        })

    return JsonResponse({"tables": data})
# -------------------------------------------------
# CLEAN TABLE
# -------------------------------------------------

@login_required
@require_POST
def mark_table_cleaned(request, table_id):

    try:

        table = Table.objects.get(
            id=table_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        table.state = "free"
        table.save(update_fields=["state"])

        return JsonResponse({"success": True})

    except Table.DoesNotExist:
        return JsonResponse({"error": "Table not found"}, status=404)

# -------------------------------------------------
# RUNNING ORDER ITEMS (used in billing screen)
# -------------------------------------------------

@login_required
def running_order_items(request):

    table_id = request.GET.get("table")

    if not table_id:
        return JsonResponse({"items": []})

    order = (
        Order.objects
        .filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            table_id=table_id,
            status__in=["open", "billing"]
        )
        .prefetch_related("items__menu_item")
        .first()
    )

    if not order:
        return JsonResponse({"items": []})

    items = []

    for i in order.items.exclude(status="served"):

        items.append({
            "id": i.id,
            "name": i.menu_item.name,
            "quantity": i.quantity,
            "status": i.status
        })

    return JsonResponse({"items": items})


# -------------------------------------------------
# RUNNING ORDER PAGE
# -------------------------------------------------

@login_required
def running_order_view(request, order_id):

    order = Order.objects.filter(
        id=order_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).first()

    if not order:
        return JsonResponse({"error": "Order not found"}, status=404)

    return render(
        request,
        "orders/running_order.html",
        {"order": order}
    )


# -------------------------------------------------
# RUNNING ORDER LIVE DATA
# -------------------------------------------------

@login_required
def running_order_data(request, order_id):

    order = (
        Order.objects
        .select_related("table")
        .prefetch_related("items__menu_item")
        .filter(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        .first()
    )

    if not order:
        return JsonResponse({"error": "Order not found"}, status=404)

    order.recalculate_totals()

    items = []

    for i in order.items.all():

        items.append({
            "id": i.id,
            "name": i.menu_item.name,
            "quantity": i.quantity,
            "status": i.status
        })

    return JsonResponse({
        "subtotal": float(order.subtotal),
        "gst": float(order.gst_total),
        "total": float(order.grand_total),
        "items": items
    })
    
    

from django.utils import timezone
from datetime import timedelta

@transaction.atomic
def lock_order(order, user):

    now = timezone.now()

    lock = getattr(order, "lock", None)

    if lock and lock.expires_at > now:
        return False

    OrderLock.objects.update_or_create(
        order=order,
        defaults={
            "locked_by": user,
            "expires_at": now + timedelta(seconds=30)
        }
    )

    return True



# -------------------------------------------------
# GENERATE BILL
# -------------------------------------------------

@login_required
@require_POST
def generate_bill(request, table_id):

    order = (
        Order.objects
        .filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            table_id=table_id,
            status="open"
        )
        .first()
    )

    if not order:
        return JsonResponse({"error":"Order not found"},status=404)

    order.status = "billing"
    order.save(update_fields=["status"])

    if order.table:
        order.table.state = "billing"
        order.table.save(update_fields=["state"])

    return JsonResponse({
        "success":True,
        "order_id":order.id
    })
    
    
    
# -------------------------------------------------
# APPLY DISCOUNT (MANAGER / CASHIER ONLY)
# -------------------------------------------------

@login_required
@require_POST
def apply_discount(request, order_id):

    if request.user.role not in ["manager","cashier","owner"]:
        return JsonResponse({"error":"Permission denied"},status=403)

    try:

        data = json.loads(request.body)
        percent = float(data.get("percent",0))

        if percent < 0 or percent > 100:
            return JsonResponse({"error":"Invalid percentage"},status=400)

        with transaction.atomic():

            order = (
                Order.objects
                .select_for_update()
                .get(
                    id=order_id,
                    tenant=request.user.tenant,
                    outlet=request.user.outlet
                )
            )

            order.discount_type="percentage"
            order.discount_value=percent
            order.save(update_fields=["discount_type","discount_value"])

            order.recalculate_totals()

        return JsonResponse({
            "success":True,
            "subtotal":float(order.subtotal),
            "gst":float(order.gst_total),
            "discount":float(order.discount_total),
            "total":float(order.grand_total)
        })

    except Exception as e:
        return JsonResponse({"error":str(e)},status=400)
    


@login_required
@require_POST
def make_item_complimentary(request, item_id):

    if request.user.role not in ["manager","owner"]:
        return JsonResponse({"error":"Permission denied"},status=403)

    try:

        item = (
            OrderItem.objects
            .select_related("order")
            .get(
                id=item_id,
                order__tenant=request.user.tenant,
                order__outlet=request.user.outlet
            )
        )

        item.is_complimentary = True
        item.save(update_fields=["is_complimentary"])

        item.order.recalculate_totals()

        return JsonResponse({"success":True})

    except OrderItem.DoesNotExist:

        return JsonResponse({"error":"Item not found"},status=404)
    