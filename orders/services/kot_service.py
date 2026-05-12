# orders/services/kot_service.py
import logging
from collections import defaultdict
from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger("pos.orders")

from orders.models import KOTBatch, OrderItem, DailyKOTCounter
from orders.services.inventory_service import deduct_inventory_for_items
from setup.services.station_service import get_default_station


@transaction.atomic
def create_kot(user, order):

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
    business_date = get_business_date(timezone.now(), order.outlet)

    counter, _ = (
        DailyKOTCounter.objects
        .select_for_update()
        .get_or_create(date=business_date, tenant=user.tenant, outlet=user.outlet)
    )

    created_kots = []

    # -----------------------------------------
    # CREATE KOT PER STATION
    # -----------------------------------------

    print_jobs = []

    for station_id, group_items in station_groups.items():

        # increment safely
        counter.value += 1
        counter.save(update_fields=["value"])

        kot_number = counter.value
        

        station = group_items[0].menu_item.station
        
        if not station :
            station = get_default_station(user)

        kot = KOTBatch.objects.create(
            tenant=user.tenant,
            outlet=user.outlet,
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
        # -----------------------------------------
        if station and station.printer_ip:
            print_jobs.append((station, kot))

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
        import threading
        from orders.tasks import print_kot_task
        for station, kot in print_jobs:
            try:
                # Run the print task in a background thread to prevent blocking
                threading.Thread(
                    target=print_kot_task, 
                    args=(station.id, order.id, kot.id),
                    daemon=True
                ).start()
            except Exception as e:
                logger.error(f"Failed to spawn print thread for KOT #{kot.kot_number}: {e}")

    transaction.on_commit(dispatch_prints)

    return created_kots