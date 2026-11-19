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
    order to a table: the waiter-side flows (billing_core.py, order_views.py)
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


class MergeError(Exception):
    """A merge that cannot be made: a table that isn't this outlet's."""


class MergeRefused(MergeError):
    """A merge the floor cannot take as it stands: a table already in a
    merge, or one with its own open order (19 Nov 2026)."""


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
        raise MergeError("Primary table not found")

    tables = [t for t in locked.values() if t.id != primary_table_id]

    # Under the tables' locks, so two merges over the same tables queue.
    from django.db.models import Q
    already = (TableMerge.objects
               .filter(tenant=user.tenant, outlet=user.outlet, is_active=True)
               .filter(Q(primary_table_id__in=all_ids) | Q(tables__id__in=all_ids))
               .distinct().first())
    if already is not None:
        names = sorted({t.name for t in locked.values()
                        if t.id == already.primary_table_id or already.tables.filter(id=t.id).exists()})
        raise MergeRefused(f"{', '.join(names)} is already merged. Unmerge it first.")
    # A table merged in keeps any order of its own, off the main table's
    # bill: the party would be billed twice over, or not at all.
    own = (Order.objects.filter(table_id__in=[t.id for t in tables], status__in=["open", "billing"])
           .select_related("table").first())
    if own is not None:
        raise MergeRefused(f"{own.table.name} has its own order. Transfer it or bill it first, then merge.")

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

    # select_for_update: without this, two near-simultaneous unmerge
    # requests for the same merge (a double-tap, or a retry on a flaky
    # connection) can both pass the view's own existence check, then race
    # here -- the second one's .get(is_active=True) raises DoesNotExist
    # once the first commits, a genuine unhandled 500 rather than a clean
    # "already unmerged" response.
    merge = (
        TableMerge.objects
        .select_for_update()
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

    # Was status="open" only -- a "billing" order (bill generated, not yet
    # paid) was invisible to this filter, so unmerging mid-billing always
    # forced every secondary table to "free" as if there were no active
    # order at all, even though the combined party's bill was still open.
    # Matches the status__in=["open","billing"] convention every other
    # order lookup in this app already uses.
    order = Order.objects.filter(
        table=primary,
        status__in=["open", "billing"]
    ).first()

    for table in merge.tables.all():

        if table == primary:
            continue

        # Never clobber a table that's already mid-billing/cleaning on its
        # own -- same guard update_table_state() uses elsewhere. A
        # secondary table can only be in a state derived from the merge
        # itself, so this only matters if something outside the merge
        # already moved it, which should be left alone rather than
        # overwritten by unmerge.
        if table.state in ("billing", "cleaning"):
            continue
        # A table with an order of its own keeps the state that order gave
        # it. Only a tangled floor (merges made before the 19 Nov guards)
        # has one: Table 1 "joined" to a Table 2 with no order would be
        # set free with its party still eating.
        if Order.objects.filter(table=table, status__in=["open", "billing"]).exists():
            continue

        if order:
            table.state = "ordering"
        else:
            table.state = "free"

        table.save(update_fields=["state"])

    merge.is_active = False
    merge.save(update_fields=["is_active"])


@transaction.atomic
def unmerge_around(user, table_id):
    """End every active merge connected to this table, whether it is the
    main table or a joined one, and return how many ended (19 Nov 2026).

    Connected means sharing a table, followed as far as it goes: a floor
    tangled before the guards (Table 2 the main table of four merges, then
    merged into Table 1) comes apart in one tap from any of its cards.
    """
    from django.db.models import Q
    scope = TableMerge.objects.filter(tenant=user.tenant, outlet=user.outlet, is_active=True)
    found, seen, frontier = set(), set(), {int(table_id)}
    while frontier:
        seen |= frontier
        reached = (scope.filter(Q(primary_table_id__in=frontier) | Q(tables__id__in=frontier))
                   .exclude(id__in=found).distinct().prefetch_related("tables"))
        frontier = set()
        for m in reached:
            found.add(m.id)
            frontier |= ({m.primary_table_id} | {t.id for t in m.tables.all()}) - seen
    # Lock them all, in id order, before ending any: a second tap waits,
    # then finds nothing still active and ends nothing.
    ids = list(TableMerge.objects.select_for_update().filter(id__in=found, is_active=True)
               .order_by("id").values_list("id", flat=True))
    for merge_id in ids:
        unmerge_tables(user, merge_id)
    return len(ids)
