# orders/views/table_views.py
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django.db import transaction
from django.utils import timezone

from core.decorators import tenant_required
from orders.models import Order, OrderEvent, Table, TableMerge

logger = logging.getLogger("pos.orders")


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
            .select_related("table")
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

                data.append({
                    "id": table.id,
                    "name": table.name,
                    "status": status,
                    "order_id": order.id if order else None,
                    "cooking_items": cooking_items,
                    "elapsed": elapsed_minutes,
                    "merged": is_secondary or is_primary,
                    "is_primary": is_primary,
                    "merged_with_names": ", ".join(merged_with_names),
                    "primary_table": primary_table_id,
                    "primary_table_name": primary_table_name
                })
            except Exception:
                data.append({"id": table.id, "name": table.name, "status": "error",
                             "order_id": None, "cooking_items": 0, "elapsed": 0,
                             "merged": False, "primary_table": None, "primary_table_name": None})

        return JsonResponse({"tables": data})

    except Exception as e:
        return JsonResponse({"error": "tables_data_failed", "message": str(e)}, status=500)


@login_required
@require_POST
@tenant_required
def mark_table_cleaned(request, table_id):
    try:
        table = Table.objects.get(id=table_id, tenant=request.user.tenant, outlet=request.user.outlet)
        table.state = "free"
        table.save(update_fields=["state"])
        logger.info(f"User {request.user.username} marked table {table.name} as cleaned")
        return JsonResponse({"success": True})
    except Table.DoesNotExist:
        return JsonResponse({"error": "Table not found"}, status=404)


@login_required
@tenant_required
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


@login_required
@tenant_required
@require_POST
def merge_tables_view(request):
    import json
    data = json.loads(request.body)
    from orders.services.table_merge_service import merge_tables
    merge = merge_tables(request.user, data.get("primary_table"), data.get("tables"))
    logger.info(f"User {request.user.username} merged tables")
    return JsonResponse({"success": True, "merge_id": merge.id})


@login_required
@tenant_required
@require_POST
def unmerge_tables_view(request, primary_id):
    from orders.services.table_merge_service import unmerge_tables
    merge = TableMerge.objects.filter(
        primary_table_id=primary_id, tenant=request.user.tenant,
        outlet=request.user.outlet, is_active=True
    ).first()
    if not merge:
        return JsonResponse({"error": "Merge not found"}, status=404)
    unmerge_tables(request.user, merge.id)
    logger.info(f"User {request.user.username} unmerged table group {primary_id}")
    return JsonResponse({"success": True})


@login_required
@tenant_required
@require_POST
def transfer_table_view(request):
    import json
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
            new_table.state = "occupied"
            new_table.save(update_fields=["state"])

            OrderEvent.objects.create(
                tenant=tenant, outlet=outlet, order=order,
                event_type="table_transferred",
                metadata={"from_table_id": old_table.id if old_table else None, "to_table_id": new_table.id},
                created_by=request.user
            )
            logger.info(f"User {request.user.username} transferred order #{order.id} from {old_table.name if old_table else '?'} to {new_table.name}")

        return JsonResponse({"success": True, "order_id": order.id})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
