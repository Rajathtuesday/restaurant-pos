# orders/services/table_merge_service.py
from django.db import transaction
from orders.models import Order, Table, TableMerge


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