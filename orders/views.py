# orders/views.py

from heapq import merge
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db import transaction
from notifications.services.notification_service import create_notification
from django.db.models import Prefetch, Sum
from core.decorators import tenant_required
import traceback

from django.utils import timezone
from datetime import timedelta

from menu.models import MenuCategory, MenuItem
from orders.services.table_merge_service import merge_tables, unmerge_tables
from orders.services.table_transfer_service import transfer_table
from setup.models import KitchenStation

from orders.utils.order_utils import validate_order_editable


from .models import (
    Order,
    OrderEvent,
    OrderItem,
    OrderLock,
    Table,
    KOTBatch,
    TableMerge
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

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import Http404

from orders.models import Order, Table, TableMerge
from orders.services.order_lock_service import lock_order
from menu.models import MenuCategory, MenuItem
from decimal import Decimal, InvalidOperation
from core.decorators import tenant_required


@login_required
@tenant_required
def billing_view(request):

    table_id = request.GET.get("table")

    order = None

    # --------------------------------------------------
    # STEP 1: Resolve merged tables
    # --------------------------------------------------

    if table_id:

        merge = (
            TableMerge.objects
            .filter(
                tenant=request.user.tenant,
                outlet=request.user.outlet,
                is_active=True,
                tables__id=table_id
            )
            .select_related("primary_table")
            .first()
        )

        # If secondary merged table → redirect to primary
        if merge and str(table_id) != str(merge.primary_table.id):
            table_id = merge.primary_table.id

    # --------------------------------------------------
    # STEP 2: Fetch order safely
    # --------------------------------------------------

    if table_id:

        order = (
            Order.objects
            .filter(
                tenant=request.user.tenant,
                outlet=request.user.outlet,
                table_id=table_id,
                status__in=["open", "billing"]
            )
            .select_related("table")
            .first()
        )

    # --------------------------------------------------
    # STEP 3: Apply order lock
    # --------------------------------------------------

    if order:

        locked, locked_user = lock_order(order, request.user)

        if not locked:

            return render(
                request,
                "orders/order_locked.html",
                {
                    "locked_by": locked_user,
                    "order": order
                }
            )

    # --------------------------------------------------
    # STEP 4: Fetch menu
    # --------------------------------------------------

    categories = (
        MenuCategory.objects
        .filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            is_active=True
        )
        .prefetch_related(
            Prefetch(
                "items",
                queryset=MenuItem.objects.filter(is_available=True)
            )
        )
    )

    # --------------------------------------------------
    # STEP 5: Fetch tables
    # --------------------------------------------------

    tables = Table.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).order_by("name")

    # --------------------------------------------------
    # STEP 6: Render page
    # --------------------------------------------------

    return render(
        request,
        "orders/billing.html",
        {
            "categories": categories,
            "tables": tables,
            "order": order,
            "selected_table": table_id
        }
    )   
# -------------------------------------------------
# CREATE ORDER
# -------------------------------------------------

@login_required
@require_POST
@tenant_required
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
        print(traceback.format_exc())
        return JsonResponse({
            "error": str(e)
        }, status=500)

# -------------------------------------------------
# SEND ORDER TO KITCHEN
# -------------------------------------------------
@login_required
@require_POST
@tenant_required
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

            # 🔥 ONLY OPEN ALLOWED
            if order.status != "open":
                return JsonResponse({"error": "Order is locked"}, status=400)

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
@tenant_required

def kitchen_view(request):

    stations = KitchenStation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    )

    return render(request, "orders/kitchen.html", {
        "stations": stations
    })

@login_required
@tenant_required

def kitchen_data(request):
    
    station_name = request.GET.get("station")

    kots = KOTBatch.objects.filter(
        order__tenant=request.user.tenant,
        order__outlet=request.user.outlet,
        order__status="open"
    )

    # 🔥 APPLY STATION FILTER (IMPORTANT)
    if station_name:
        kots = kots.filter(station=station_name)

    # THEN optimize
    kots = (
        kots
        .select_related("order", "order__table")
        .prefetch_related("items","items__menu_item")
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
            "station": kot.station,
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
@tenant_required

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
@tenant_required
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
@tenant_required
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

@tenant_required
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
@tenant_required
def pay_order(request, order_id):

    try:
        data = json.loads(request.body)

        method = data.get("method")
        amount = data.get("amount")  # 🔥 allow partial payments

        if method not in ["cash", "upi", "card"]:
            return JsonResponse({"error": "Invalid payment method"}, status=400)

        if amount is None:
            return JsonResponse({"error": "Amount required"}, status=400)
        try:
            amount = Decimal(str(amount))
        except InvalidOperation:
            return JsonResponse({"error": "Invalid amount"}, status=400)

        if amount <= 0:
            return JsonResponse({"error": "Amount must be positive"}, status=400)

        with transaction.atomic():

            # 🔒 LOCK ORDER
            order = (
                Order.objects
                .select_for_update()
                .get(
                    id=order_id,
                    tenant=request.user.tenant,
                    outlet=request.user.outlet
                )
            )

            # ----------------------------
            # STATE VALIDATION
            # ----------------------------
            if order.status in ["paid", "closed"]:
                return JsonResponse({"error": "Order already completed"}, status=400)

            # ----------------------------
            # PROCESS PAYMENT
            # ----------------------------
            process_payment(order, method, amount, request.user)

            # 🔄 Refresh updated order
            order.refresh_from_db()

            # ----------------------------
            # IF FULLY PAID → CLOSE ORDER
            # ----------------------------
            if order.status == "paid":

                from orders.utils.payment_utils import validate_order_payment

                # Safety check
                validate_order_payment(order)

                # ----------------------------
                # CLOSE ORDER
                # ----------------------------
                order.status = "closed"
                order.closed_at = timezone.now()
                order.save(update_fields=["status", "closed_at"])

                # ----------------------------
                # MOVE TABLE STATE
                # ----------------------------
                if order.table:
                    order.table.state = "cleaning"
                    order.table.save(update_fields=["state"])

                return JsonResponse({
                    "success": True,
                    "message": "Payment complete, order closed"
                })

            # ----------------------------
            # PARTIAL PAYMENT RESPONSE
            # ----------------------------
            remaining = order.grand_total - (
                order.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
            )

            return JsonResponse({
                "success": True,
                "message": "Partial payment recorded",
                "remaining": remaining
            })

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
    
# -------------------------------------------------
# TABLE DASHBOARD
# -------------------------------------------------

@login_required
@tenant_required
def table_dashboard(request):
    return render(request, "orders/tables.html")

@login_required
@tenant_required
def tables_data(request):

    try:

        tenant = request.user.tenant
        outlet = request.user.outlet
        now = timezone.now()

        # -------------------------------------------------
        # FETCH TABLES
        # -------------------------------------------------
        tables = list(
            Table.objects.filter(
                tenant=tenant,
                outlet=outlet,
                is_active=True
            ).order_by("name")
        )

        table_name_lookup = {t.id: t.name for t in tables}

        # -------------------------------------------------
        # FETCH MERGES
        # -------------------------------------------------
        merges = (
            TableMerge.objects
            .filter(
                tenant=tenant,
                outlet=outlet,
                is_active=True
            )
            .select_related("primary_table")
            .prefetch_related("tables")
        )

        merged_lookup = {}

        for merge in merges:
            primary_id = merge.primary_table.id
            for t in merge.tables.all():
                if t.id != primary_id:
                    merged_lookup[t.id] = primary_id

        # -------------------------------------------------
        # FETCH ORDERS (IMPORTANT FIX)
        # -------------------------------------------------
        orders = (
            Order.objects
            .filter(
                tenant=tenant,
                outlet=outlet,
                status__in=["open", "billing"]
            )
            .select_related("table")
            .prefetch_related("items")
        )

        orders_map = {o.table_id: o for o in orders}

        # -------------------------------------------------
        # PROCESS TABLES
        # -------------------------------------------------
        data = []

        for table in tables:

            try:

                primary_table_id = merged_lookup.get(table.id)
                is_merged = False

                if primary_table_id and primary_table_id != table.id:
                    is_merged = True

                # --------------------------------------------
                # DETERMINE ORDER SOURCE
                # --------------------------------------------
                lookup_table_id = primary_table_id if primary_table_id else table.id
                order = orders_map.get(lookup_table_id)

                # --------------------------------------------
                # METRICS
                # --------------------------------------------
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

                # --------------------------------------------
                # 🔥 DERIVE STATUS (CORE FIX)
                # --------------------------------------------
                if is_merged:
                    status = "merged"

                elif table.state == "cleaning":
                    status = "cleaning"

                elif not order:
                    status = "free"

                elif order.status == "billing":
                    status = "billing"

                else:
                    items = order.items.all()

                    if not items.exists():
                        status = "ordering"

                    elif items.filter(status="pending").exists():
                        status = "ordering"

                    elif items.filter(status__in=["sent", "preparing"]).exists():
                        status = "preparing"

                    elif items.filter(status="ready").exists():
                        status = "ready"

                    elif items.filter(status="served").exists():
                        status = "served"

                    else:
                        status = "ordering"

                # --------------------------------------------
                # MERGE DISPLAY INFO
                # --------------------------------------------
                primary_table_name = None

                if primary_table_id:
                    primary_table_name = table_name_lookup.get(primary_table_id)

                # --------------------------------------------
                # APPEND
                # --------------------------------------------
                data.append({
                    "id": table.id,
                    "name": table.name,
                    "status": status,
                    "order_id": order.id if order else None,
                    "cooking_items": cooking_items,
                    "elapsed": elapsed_minutes,
                    "merged": is_merged,
                    "primary_table": primary_table_id,
                    "primary_table_name": primary_table_name
                })

            except Exception as e:

                data.append({
                    "id": table.id,
                    "name": table.name,
                    "status": "error",
                    "order_id": None,
                    "cooking_items": 0,
                    "elapsed": 0,
                    "merged": False,
                    "primary_table": None,
                    "primary_table_name": None
                })

        return JsonResponse({"tables": data})

    except Exception as e:

        return JsonResponse({
            "error": "tables_data_failed",
            "message": str(e)
        }, status=500)
# -------------------------------------------------
# CLEAN TABLE
# -------------------------------------------------

@login_required
@require_POST
@tenant_required
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
@tenant_required
def running_order_items(request):

    try:

        table_id = request.GET.get("table")

        # --------------------------------------------
        # 🔥 VALIDATE INPUT
        # --------------------------------------------
        if not table_id:
            return JsonResponse({"items": []})

        try:
            table_id = int(table_id)
        except ValueError:
            return JsonResponse({"items": []})

        tenant = request.user.tenant
        outlet = request.user.outlet

        # --------------------------------------------
        # 🔥 RESOLVE MERGE (CRITICAL)
        # --------------------------------------------
        merge = (
            TableMerge.objects
            .filter(
                tenant=tenant,
                outlet=outlet,
                is_active=True,
                tables__id=table_id
            )
            .select_related("primary_table")
            .first()
        )

        if merge:
            table_id = merge.primary_table.id

        # --------------------------------------------
        # 🔥 FETCH ORDER (STRICT + SAFE)
        # --------------------------------------------
        orders = (
            Order.objects
            .filter(
                tenant=tenant,
                outlet=outlet,
                table_id=table_id,
                status__in=["open", "billing"]
            )
            .prefetch_related("items__menu_item")
            .order_by("-created_at")
        )

        order = orders.first()

        # 🔴 SAFETY CHECK (should never happen)
        if orders.count() > 1:
            print("⚠️ Multiple active orders for table:", table_id)

        if not order:
            return JsonResponse({"items": []})

        # --------------------------------------------
        # 🔥 RETURN ITEMS (ORDERED)
        # --------------------------------------------
        items = []

        for i in order.items.exclude(status="served").order_by("created_at"):

            items.append({
                "id": i.id,
                "name": i.menu_item.name,
                "quantity": i.quantity,
                "status": i.status
            })

        return JsonResponse({"items": items})

    except Exception as e:

        return JsonResponse({
            "error": "running_order_failed",
            "message": str(e)
        }, status=500)
# -------------------------------------------------
# RUNNING ORDER PAGE
# -------------------------------------------------

@login_required
@tenant_required
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
@tenant_required
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
    
    

# -------------------------------------------------
# GENERATE BILL
# -------------------------------------------------
@login_required
@tenant_required
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

    # 🔥 PREVENT DOUBLE BILLING
    if order.status != "open":
        return JsonResponse({"error": "Order already locked"}, status=400)

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
@tenant_required
@require_POST
def apply_discount(request, order_id):

    if request.user.role not in ["manager","cashier","owner"]:
        return JsonResponse({"error":"Permission denied"},status=403)

    try:
        from orders.utils.order_utils import validate_order_editable

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

            # 🔥 LOCK CHECK
            validate_order_editable(order)

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
@tenant_required
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

        validate_order_editable(item.order)

        item.is_complimentary = True
        item.save(update_fields=["is_complimentary"])

        item.order.recalculate_totals()

        return JsonResponse({"success":True})

    except OrderItem.DoesNotExist:

        return JsonResponse({"error":"Item not found"},status=404)
    
    
    
@login_required
@tenant_required
@require_POST
def merge_tables_view(request):

    data = json.loads(request.body)

    primary = data.get("primary_table")
    tables = data.get("tables")

    merge = merge_tables(
        request.user,
        primary,
        tables
    )

    return JsonResponse({
        "success": True,
        "merge_id": merge.id
    })
    
    

@login_required
@tenant_required

@require_POST
def unmerge_tables_view(request, primary_id):

    merge = TableMerge.objects.filter(
        primary_table_id=primary_id,
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_active=True
    ).first()

    if not merge:
        return JsonResponse({"error": "Merge not found"}, status=404)

    unmerge_tables(request.user, merge.id)

    return JsonResponse({"success": True})


from django.db import transaction

@login_required
@tenant_required
@require_POST
def transfer_table_view(request):

    try:

        data = json.loads(request.body)

        order_id = data.get("order_id")
        table_id = data.get("table_id")

        if not order_id or not table_id:
            return JsonResponse({"error": "Missing parameters"}, status=400)

        try:
            order_id = int(order_id)
            table_id = int(table_id)
        except ValueError:
            return JsonResponse({"error": "Invalid IDs"}, status=400)

        tenant = request.user.tenant
        outlet = request.user.outlet

        # -------------------------------------------------
        # 🔥 CRITICAL: ATOMIC TRANSACTION
        # -------------------------------------------------
        with transaction.atomic():

            # --------------------------------------------
            # 🔒 LOCK ORDER
            # --------------------------------------------
            order = (
                Order.objects
                .select_for_update()
                .filter(
                    id=order_id,
                    tenant=tenant,
                    outlet=outlet
                )

                .first()
            )

            if not order:
                return JsonResponse({"error": "Order not found"}, status=404)

            if order.status in ["billing", "paid", "closed"]:
                return JsonResponse({"error": "Cannot transfer at this stage"}, status=400)

            # --------------------------------------------
            # 🔥 FETCH NEW TABLE
            # --------------------------------------------
            new_table = Table.objects.filter(
                id=table_id,
                tenant=tenant,
                outlet=outlet,
                is_active=True
            ).first()

            if not new_table:
                return JsonResponse({"error": "Invalid table"}, status=400)

            if new_table.id == order.table_id:
                return JsonResponse({"error": "Same table"}, status=400)

            # --------------------------------------------
            # 🔥 BLOCK MERGED TABLES
            # --------------------------------------------
            is_merged = TableMerge.objects.filter(
                tenant=tenant,
                outlet=outlet,
                is_active=True,
                tables=new_table
            ).exists()

            if is_merged:
                return JsonResponse({"error": "Cannot transfer to merged table"}, status=400)

            # --------------------------------------------
            # 🔥 CHECK OCCUPANCY (REAL CHECK)
            # --------------------------------------------
            occupied = Order.objects.filter(
                tenant=tenant,
                outlet=outlet,
                table=new_table,
                status__in=["open", "billing"]
            ).exists()

            if occupied:
                return JsonResponse({"error": "Table already occupied"}, status=400)

            # --------------------------------------------
            # 🔥 PERFORM TRANSFER
            # --------------------------------------------
            old_table = order.table

            order.table = new_table
            order.save(update_fields=["table"])

            # --------------------------------------------
            # 🔥 UPDATE TABLE STATES (SECONDARY ONLY)
            # --------------------------------------------
            if old_table:
                old_table.state = "free"
                old_table.save(update_fields=["state"])

            new_table.state = "occupied"
            new_table.save(update_fields=["state"])

            # --------------------------------------------
            # 🔥 AUDIT EVENT (PRODUCTION SAFE)
            # --------------------------------------------
            OrderEvent.objects.create(
                tenant=tenant,
                outlet=outlet,
                order=order,
                event_type="table_transferred",
                metadata={
                    "from_table_id": old_table.id if old_table else None,
                    "to_table_id": new_table.id
                },
                created_by=request.user
            )

        return JsonResponse({
            "success": True,
            "order_id": order.id
        })

    except Exception as e:

        return JsonResponse({
            "error": str(e)
        }, status=400)    
    

@login_required
@tenant_required    
def available_tables(request):

    tenant = request.user.tenant
    outlet = request.user.outlet

    # --------------------------------------------
    # 🔥 GET ACTIVE ORDERS (REAL OCCUPANCY)
    # --------------------------------------------
    active_table_ids = set(
        Order.objects.filter(
            tenant=tenant,
            outlet=outlet,
            status__in=["open", "billing"]
        ).values_list("table_id", flat=True)
    )

    # --------------------------------------------
    # 🔥 GET MERGED TABLES (BLOCK THEM)
    # --------------------------------------------
    merged_table_ids = set(
        TableMerge.objects.filter(
            tenant=tenant,
            outlet=outlet,
            is_active=True
        ).values_list("tables__id", flat=True)
    )

    # --------------------------------------------
    # 🔥 FILTER AVAILABLE TABLES
    # --------------------------------------------
    tables = (
        Table.objects
        .filter(
            tenant=tenant,
            outlet=outlet,
            is_active=True
        )
        .exclude(id__in=active_table_ids)
        .exclude(id__in=merged_table_ids)
        .values("id", "name")
    )

    return JsonResponse({
        "tables": list(tables)
    })
    
    
    
@login_required
@require_POST
@tenant_required
def refund_payment(request, order_id):

    try:

        data = json.loads(request.body)

        payment_id = data.get("payment_id")
        amount = data.get("amount")

        order = Order.objects.get(
            id=order_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )

        from orders.services.refund_service import process_refund

        refund = process_refund(
            order,
            payment_id,
            amount,
            request.user
        )

        return JsonResponse({
            "success": True,
            "refund_id": refund.id
        })

    except Exception as e:

        return JsonResponse({
            "error": str(e)
        }, status=400)