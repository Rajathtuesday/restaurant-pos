# orders/services/kot_service.py
import logging
from collections import defaultdict
from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger("pos.orders")

from orders.models import KOTBatch, OrderItem, DailyKOTCounter
from orders.services.inventory_service import deduct_inventory_for_items
from setup.services.station_service import get_default_station_for


@transaction.atomic
def create_kot(user, order, print_on_create=True):
    """
    print_on_create=False: create KOT records but skip queuing print tasks.
    Used by pay_order() in auto-KOT mode — print_bill_task handles everything.

    `user` may be None (e.g. aggregator/webhook order ingestion, where there
    is no logged-in staff member). Tenant/outlet are taken from the order
    itself, not the user — the KOT belongs to the order's outlet regardless of
    who triggered it, which is also strictly more correct for the staff path.
    """
    tenant = order.tenant
    outlet = order.outlet

    # -----------------------------------------
    # LOCK ORDER ITEMS + LOAD RELATIONS IN ONE QUERY
    # -----------------------------------------

    items = list(
        OrderItem.objects
        .select_for_update(of=("self",))
        .filter(order=order, status="pending")
        .select_related("menu_item", "menu_item__station")
    )

    if not items:
        raise Exception("No items to send to kitchen")

    # -----------------------------------------
    # GROUP ITEMS BY STATION
    # -----------------------------------------

    station_groups = defaultdict(list)

    for item in items:

        
        station = item.menu_item.station

        station_key = station.id if station else "default"

        station_groups[station_key].append(item)

    # -----------------------------------------
    # DAILY KOT COUNTER LOCK
    # -----------------------------------------
    from core.utils import get_business_date
    business_date = get_business_date(timezone.now(), outlet)

    counter, _ = (
        DailyKOTCounter.objects
        .select_for_update()
        .get_or_create(date=business_date, tenant=tenant, outlet=outlet)
    )

    created_kots = []

    # -----------------------------------------
    # CREATE KOT PER STATION
    # -----------------------------------------

    print_jobs = []
    # Resolved once — used as fallback printer for stations without their own IP.
    _default_station_cache = None

    def _default_station():
        nonlocal _default_station_cache
        if _default_station_cache is None:
            _default_station_cache = get_default_station_for(tenant, outlet)
        return _default_station_cache

    for station_id, group_items in station_groups.items():

        # increment safely
        counter.value += 1
        counter.save(update_fields=["value"])

        kot_number = counter.value

        station = group_items[0].menu_item.station
        if not station:
            station = _default_station()

        kot = KOTBatch.objects.create(
            tenant=tenant,
            outlet=outlet,
            order=order,
            kot_number=kot_number,
            station=station,
            status="confirmed"
        )

        # -----------------------------------------
        # BULK DEDUCT INVENTORY
        # -----------------------------------------
        deduct_inventory_for_items(group_items)

        for item in group_items:
            item.kot = kot
            item.status = "sent"
            item.save(update_fields=["kot", "status"])

        created_kots.append(kot)

        # -----------------------------------------
        # QUEUE KOT PRINTING (OUTSIDE TRANSACTION)
        # If this station has no printer, fall back to the default station's
        # printer — this is the single-printer QSR pattern where one counter
        # printer handles all KOTs and bills.
        # -----------------------------------------
        printer_station = station if (station and station.printer_ip) else _default_station()
        if printer_station and printer_station.printer_ip:
            print_jobs.append((printer_station, kot))

    # -----------------------------------------
    # UPDATE TABLE STATE
    # -----------------------------------------

    if order.table:

        table = order.table

        table.state = "preparing"

        table.save(update_fields=["state"])

    # -----------------------------------------
    # EXECUTE PRINTING (ASYNC THREAD ON COMMIT)
    # -----------------------------------------
    def dispatch_prints():
        if not print_on_create:
            # Caller (pay_order auto-KOT mode) handles printing via print_bill_task
            return
        from orders.tasks import print_kot_task
        for station, kot in print_jobs:
            try:
                print_kot_task.delay(station.id, order.id, kot.id)
            except Exception as exc:
                logger.warning(
                    "Celery unavailable for KOT #%s, falling back to sync print: %s",
                    kot.kot_number, exc,
                )
                try:
                    print_kot_task(station.id, order.id, kot.id)
                except Exception as sync_exc:
                    logger.error("Sync fallback also failed for KOT #%s: %s", kot.kot_number, sync_exc)

    transaction.on_commit(dispatch_prints)

    return created_kots