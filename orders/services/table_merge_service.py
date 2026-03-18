from django.db import transaction
from orders.models import Order, Table, TableMerge


@transaction.atomic
def merge_tables(user, primary_table_id, table_ids):

    primary_table = Table.objects.get(
        id=primary_table_id,
        tenant=user.tenant,
        outlet=user.outlet
    )

    # remove primary from list
    tables = Table.objects.filter(
        id__in=table_ids,
        tenant=user.tenant,
        outlet=user.outlet
    ).exclude(id=primary_table_id)

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

    merge = TableMerge.objects.select_related("primary_table").prefetch_related("tables").get(
        id=merge_id,
        tenant=user.tenant,
        outlet=user.outlet,
        is_active=True
    )

    primary = merge.primary_table

    # restore tables properly
    for table in merge.tables.all():

        order_exists = Order.objects.filter(
            table=table,
            status="open"
        ).exists()

        if order_exists:
            table.state = "ordering"
        else:
            table.state = "free"

        table.save(update_fields=["state"])

    merge.is_active = False
    merge.save(update_fields=["is_active"])