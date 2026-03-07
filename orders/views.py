# orders/views.py

import json
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db import transaction
from notifications.services.notification_service import create_notification

from menu.models import MenuCategory

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

@login_required
def billing_view(request):

    categories = (
        MenuCategory.objects
        .filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            is_active=True
        )
        .prefetch_related("items")
    )

    tables = Table.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    )

    selected_table = request.GET.get("table")

    return render(
        request,
        "orders/billing.html",
        {
            "categories": categories,
            "tables": tables,
            "selected_table": selected_table
        }
    )


# -------------------------------------------------
# CREATE ORDER
# -------------------------------------------------

@login_required
@require_POST
def create_order(request):

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    cart = data.get("cart", [])
    table_id = data.get("table_id")

    if not cart:
        return JsonResponse({"error": "Cart empty"}, status=400)

    table = None

    if table_id:
        table = Table.objects.filter(
            id=table_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        ).first()

        if not table:
            return JsonResponse({"error": "Invalid table"}, status=400)

    try:

        order = get_or_create_open_order(request.user, table)

        add_items_to_order(request.user, order, cart)

        return JsonResponse({
            "success": True,
            "order_id": order.id
        })

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# -------------------------------------------------
# SEND ORDER TO KITCHEN
# -------------------------------------------------

@login_required
@require_POST
def send_to_kitchen(request, order_id):

    try:

        order = Order.objects.get(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            status="open"
        )

        kot = create_kot(request.user, order)

        return JsonResponse({
            "success": True,
            "kot_number": kot.kot_number
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

        for i in kot.items.exclude(status="served"):

            items.append({
                "id": i.id,
                "name": i.menu_item.name,
                "quantity": i.quantity,
                "status": i.status
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


@login_required
@require_POST
def mark_ready(request, item_id):

    try:

        item = OrderItem.objects.get(
            id=item_id,
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet
        )
        

        if item.status != "preparing":
            return JsonResponse({"error": "Invalid state"}, status=400)

        item.status = "ready"
        item.save(update_fields=["status"])

        update_table_state(item.order)

        create_notification(
            item.order.tenant,
            item.order.outlet,
            "order_ready",
            f"Order ready for {item.order.table.name}"
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

        return render(request, "orders/bill.html", {"order": order})

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
        amount = data.get("amount")

        order = Order.objects.get(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

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

    tables = Table.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    )

    data = []

    for table in tables:

        order = (
            Order.objects
            .filter(
                tenant=request.user.tenant,
                outlet=request.user.outlet,
                table=table,
                status="open"
            )
            .prefetch_related("items")
            .first()
        )

        status = table.state

        cooking_items = 0
        elapsed_minutes = 0

        if order:

            cooking_items = order.items.filter(
                status__in=["sent", "preparing"]
            ).count()

            elapsed_minutes = int(
                (timezone.now() - order.created_at).total_seconds() / 60
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
# RUNNING ORDER ITEMS
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
            status="open"
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
    
    
@login_required
def running_order_data(request, order_id):

    order = (
        Order.objects
        .select_related("table")
        .prefetch_related("items__menu_item")
        .get(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
    )

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