# orders/views/table_views.py
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db import transaction
from django.utils import timezone
import json

from core.decorators import tenant_required, role_required, feature_required
from orders.models import Order, OrderEvent, Table
from tablemerge.models import TableMerge

logger = logging.getLogger("pos.orders")


@login_required
@tenant_required
@feature_required("floor_plan")
def table_dashboard(request):
    return render(request, "orders/tables.html")


@login_required
@tenant_required
@feature_required("floor_plan")
def tables_data(request):
    try:
        tenant = request.user.tenant
        outlet = request.user.outlet
        now = timezone.now()

        tables = list(Table.objects.filter(tenant=tenant, outlet=outlet, is_active=True).order_by("name"))
        table_name_lookup = {t.id: t.name for t in tables}

        merges = (
            TableMerge.objects
            .filter(tenant=tenant, outlet=outlet, is_active=True)
            .select_related("primary_table")
            .prefetch_related("tables")
        )
        merged_lookup = {}
        primary_lookup = {}
        for merge in merges:
            primary_id = merge.primary_table.id
            primary_name = merge.primary_table.name
            primary_lookup[primary_id] = [t.name for t in merge.tables.all() if t.id != primary_id]
            for t in merge.tables.all():
                if t.id != primary_id:
                    merged_lookup[t.id] = (primary_id, primary_name)

        orders = (
            Order.objects
            .filter(tenant=tenant, outlet=outlet, status__in=["open", "billing"])
            .select_related("table", "created_by")
            .prefetch_related("items")
        )
        orders_map = {o.table_id: o for o in orders}

        data = []
        for table in tables:
            try:
                merge_info = merged_lookup.get(table.id)
                is_secondary = bool(merge_info)
                primary_table_id = merge_info[0] if merge_info else None
                primary_table_name = merge_info[1] if merge_info else None
                
                is_primary = table.id in primary_lookup
                merged_with_names = primary_lookup.get(table.id, [])

                lookup_table_id = primary_table_id if primary_table_id else table.id
                order = orders_map.get(lookup_table_id)

                cooking_items = 0
                elapsed_minutes = 0
                if order:
                    cooking_items = sum(1 for i in order.items.all() if i.status in ["sent", "preparing"])
                    elapsed_minutes = int((now - order.created_at).total_seconds() / 60)

                if is_secondary:
                    status = "merged"
                elif table.state == "cleaning":
                    status = "cleaning"
                elif not order:
                    if table.state == "ordering":
                        # A seated reservation nudges the table here before
                        # any Order exists (crm's update_reservation_status) --
                        # the only non-"free" state that's legitimate with no
                        # order behind it.
                        status = "ordering"
                    elif table.state == "free":
                        status = "free"
                    else:
                        # Any other state (billing/preparing/ready/served)
                        # implies an Order should exist. With none, it's stale
                        # -- most commonly bill_view() sets table.state =
                        # "billing" on every page load (not just on payment),
                        # so a cashier who opens a bill and navigates away
                        # without paying or cancelling leaves the table stuck
                        # showing "Billing"/"Pay Bill" indefinitely, with no
                        # order to actually pay. Self-heal rather than surface
                        # a misleading label -- matches the self-heal pattern
                        # already used for a missing DailyOnlineTokenCounter
                        # row in tokens/views.py::assign_online_token.
                        table.state = "free"
                        table.save(update_fields=["state"])
                        status = "free"
                elif order.status == "billing":
                    status = "billing"
                else:
                    # Convert to list to use prefetch cache. Voided items are
                    # excluded — they're inert and must never keep a table
                    # looking active (same fix as update_table_state).
                    items_list = [i for i in order.items.all() if i.status != "voided"]

                    if not items_list:
                        status = "free"
                    elif any(i.status == "review" for i in items_list):
                        status = "needs_approval"
                    elif any(i.status == "pending" for i in items_list):
                        status = "ordering"
                    elif any(i.status in ["sent", "preparing"] for i in items_list):
                        status = "preparing"
                    elif any(i.status == "ready" for i in items_list):
                        status = "ready"
                    elif any(i.status == "served" for i in items_list):
                        status = "served"
                    else:
                        status = "ordering"

                data.append({
                    "id": table.id,
                    "name": table.name,
                    "section": table.section,
                    "qr_token": str(table.qr_token),
                    "status": status,
                    "order_id": order.id if order else None,
                    "cooking_items": cooking_items,
                    "elapsed": elapsed_minutes,
                    "merged": is_secondary or is_primary,
                    "is_primary": is_primary,
                    "merged_with_names": ", ".join(merged_with_names),
                    "primary_table": primary_table_id,
                    "primary_table_name": primary_table_name,
                    "waiter_name": (order.created_by.get_full_name() or order.created_by.username) if order and order.created_by else ("Guest (QR)" if order else ""),
                    "waiter_initials": "".join([n[0] for n in (order.created_by.get_full_name() or order.created_by.username).split()])[:2].upper() if order and order.created_by else ("QR" if order else "")
                })
            except Exception as e:
                data.append({"id": table.id, "name": table.name, "section": table.section, "status": "error",
                             "order_id": None, "cooking_items": 0, "elapsed": 0,
                             "merged": False, "primary_table": None, "primary_table_name": None})

        return JsonResponse({"tables": data})

    except Exception:
        logger.exception("tables_data failed")
        return JsonResponse({"error": "tables_data_failed", "message": "Could not load table data. Please try again."}, status=500)


@login_required
@require_POST
@tenant_required
@feature_required("floor_plan")
def mark_table_cleaned(request, table_id):
    try:
        table = Table.objects.get(id=table_id, tenant=request.user.tenant, outlet=request.user.outlet)
        table.state = "free"
        table.save(update_fields=["state"])
        logger.info("User %s marked table %s as cleaned", request.user.username, table.name)
        return JsonResponse({"success": True})
    except Table.DoesNotExist:
        return JsonResponse({"error": "Table not found"}, status=404)


@login_required
@tenant_required
@feature_required("floor_plan")
def available_tables(request):
    tenant = request.user.tenant
    outlet = request.user.outlet
    active_table_ids = set(
        Order.objects.filter(tenant=tenant, outlet=outlet, status__in=["open", "billing"])
        .values_list("table_id", flat=True)
    )
    merged_table_ids = set(
        TableMerge.objects.filter(tenant=tenant, outlet=outlet, is_active=True)
        .values_list("tables__id", flat=True)
    )
    tables = (
        Table.objects.filter(tenant=tenant, outlet=outlet, is_active=True)
        .exclude(id__in=active_table_ids)
        .exclude(id__in=merged_table_ids)
        .values("id", "name")
    )
    return JsonResponse({"tables": list(tables)})


# merge_tables_view/unmerge_tables_view moved to tablemerge/views.py
# (Phase 5 of the orders app split) -- TableMerge is still imported above
# because tables_data/available_tables/transfer_table_view below all read
# it directly.


@login_required
@tenant_required
@feature_required("floor_plan")
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
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid IDs"}, status=400)

        tenant = request.user.tenant
        outlet = request.user.outlet

        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .filter(id=order_id, tenant=tenant, outlet=outlet).first()
            )
            if not order:
                return JsonResponse({"error": "Order not found"}, status=404)
            if order.status in ["billing", "paid", "closed"]:
                return JsonResponse({"error": "Cannot transfer at this stage"}, status=400)

            new_table = Table.objects.filter(id=table_id, tenant=tenant, outlet=outlet, is_active=True).first()
            if not new_table:
                return JsonResponse({"error": "Invalid table"}, status=400)
            if new_table.id == order.table_id:
                return JsonResponse({"error": "Same table"}, status=400)

            if TableMerge.objects.filter(tenant=tenant, outlet=outlet, is_active=True, tables=new_table).exists():
                return JsonResponse({"error": "Cannot transfer to merged table"}, status=400)

            if Order.objects.filter(tenant=tenant, outlet=outlet, table=new_table, status__in=["open", "billing"]).exists():
                return JsonResponse({"error": "Table already occupied"}, status=400)

            old_table = order.table
            order.table = new_table
            order.save(update_fields=["table"])

            if old_table:
                old_table.state = "free"
                old_table.save(update_fields=["state"])
            new_table.state = "ordering"
            new_table.save(update_fields=["state"])

            OrderEvent.objects.create(
                tenant=tenant, outlet=outlet, order=order,
                event_type="table_transferred",
                metadata={"from_table_id": old_table.id if old_table else None, "to_table_id": new_table.id},
                created_by=request.user
            )
            logger.info(
                "User %s transferred order #%s from %s to %s",
                request.user.username, order.id,
                old_table.name if old_table else '?', new_table.name,
            )

        return JsonResponse({"success": True, "order_id": order.id})

    except Exception:
        logger.exception("Error transferring table")
        return JsonResponse({"error": "Could not transfer the table. Please try again."}, status=400)


@login_required
@tenant_required
@feature_required("floor_plan")
@role_required("manager", "owner")
@require_POST
def manage_table_view(request):
    try:
        data = json.loads(request.body)
        table_id = data.get("table_id")
        action = data.get("action")
        
        tenant = request.user.tenant
        outlet = request.user.outlet
        
        if action == "create":
            name = data.get("name")
            if not name:
                return JsonResponse({"error": "Table name is required"}, status=400)
            section = data.get("section", "Main Hall")
            Table.objects.create(name=name, section=section, tenant=tenant, outlet=outlet)
            return JsonResponse({"success": True})
            
        table = Table.objects.get(id=table_id, tenant=tenant, outlet=outlet)
        
        if action == "edit":
            name = data.get("name")
            section = data.get("section")
            if name: table.name = name
            if section: table.section = section
            table.save()
            return JsonResponse({"success": True})

        # Any other action falls through here. Without this explicit return the
        # function returns None, which Django turns into a 500 ("didn't return
        # an HttpResponse") instead of a clean 400.
        return JsonResponse({"error": f"Unknown action: {action}"}, status=400)

    except Exception:
        logger.exception("Error managing table")
        return JsonResponse({"error": "Could not complete that action. Please try again."}, status=400)
