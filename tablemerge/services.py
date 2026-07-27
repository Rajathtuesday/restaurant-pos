# tablemerge/services.py
# Moved from orders/services/table_merge_service.py (Phase 5 of the orders
# app split).
from django.db import transaction
from orders.models import Order, Table
from tablemerge.models import TableMerge


def resolve_primary_table(table, tenant, outlet):
    """
    If `table` is currently a secondary member of an active TableMerge,
    return the merge's primary table instead. Otherwise return `table`
    unchanged.

    Every order-creation entry point must call this before attaching an
    order to a table — the waiter-side flows (billing_core.py, order_views.py)
    each independently reimplement this same lookup inline, but the QR
    guest-ordering path (billing_views.py::create_order) never did, so a
    guest scanning the QR code physically stuck to a merged secondary table
    got an order created against that table alone instead of the merged
    group.
    """
    if not table:
        return table
    merge = (
        TableMerge.objects
        .filter(tenant=tenant, outlet=outlet, is_active=True, tables__id=table.id)
        .select_related("primary_table")
        .first()
    )
    return merge.primary_table if merge else table


@transaction.atomic
def merge_tables(user, primary_table_id, table_ids):

    # Lock all involved tables in consistent ID order to prevent deadlocks
    # from concurrent merge requests that overlap the same tables.
    all_ids = sorted(set([primary_table_id] + list(table_ids)))
    locked = {
        t.id: t
        for t in Table.objects.select_for_update().filter(
            id__in=all_ids,
            tenant=user.tenant,
            outlet=user.outlet
        )
    }

    primary_table = locked.get(primary_table_id)
    if not primary_table:
        raise Exception("Primary table not found")

    tables = [t for t in locked.values() if t.id != primary_table_id]

    merge = TableMerge.objects.create(
        tenant=user.tenant,
        outlet=user.outlet,
        primary_table=primary_table,
        created_by=user
    )

    merge.tables.set(tables)

    for t in tables:

        if t.state == "free":
            t.state = "ordering"
            t.save(update_fields=["state"])

    return merge


# ---------------------------------
# UNMERGE TABLES
# ---------------------------------


@transaction.atomic
def unmerge_tables(user, merge_id):

    merge = (
        TableMerge.objects
        .select_related("primary_table")
        .prefetch_related("tables")
        .get(
            id=merge_id,
            tenant=user.tenant,
            outlet=user.outlet,
            is_active=True
        )
    )

    primary = merge.primary_table

    # get active order of the primary table
    order = Order.objects.filter(
        table=primary,
        status="open"
    ).first()

    for table in merge.tables.all():

        if table == primary:
            continue

        # restore proper state
        if order:
            table.state = "ordering"
        else:
            table.state = "free"

        table.save(update_fields=["state"])

    merge.is_active = False
    merge.save(update_fields=["is_active"])
