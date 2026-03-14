# orders/services/kot_service.py
from collections import defaultdict
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from orders.models import KOTBatch, OrderItem, DailyKOTCounter
from orders.services.inventory_service import deduct_inventory


@transaction.atomic
def create_kot(user, order):

    # -----------------------------------------
    # LOCK ONLY ORDER ITEMS
    # -----------------------------------------

    items = (
        OrderItem.objects
        .select_for_update(of=("self",))
        .filter(
            order=order,
            status="pending"
        )
    )

    if not items.exists():
        raise Exception("No items to send to kitchen")

    # -----------------------------------------
    # LOAD RELATIONS AFTER LOCK
    # -----------------------------------------

    items = items.select_related(
        "menu_item",
        "menu_item__station"
    )

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

    today = timezone.now().date()

    counter, _ = (
        DailyKOTCounter.objects
        .select_for_update()
        .get_or_create(date=today)
    )

    created_kots = []

    # -----------------------------------------
    # CREATE KOT PER STATION
    # -----------------------------------------

    for station_id, group_items in station_groups.items():

        # increment safely
        counter.value += 1
        counter.save(update_fields=["value"])
        counter.refresh_from_db()  # ensure we have the latest value after save

        kot_number = counter.value
        

        station = group_items[0].menu_item.station

        station_name = station.name if station else "General"

        kot = KOTBatch.objects.create(
            tenant=user.tenant,
            outlet=user.outlet,
            order=order,
            kot_number=kot_number,
            station=station_name,
            status="confirmed"
        )

        for item in group_items:

            # deduct inventory
            deduct_inventory(item)

            item.kot = kot
            item.status = "sent"

            item.save(update_fields=["kot", "status"])

        created_kots.append(kot)

    # -----------------------------------------
    # UPDATE TABLE STATE
    # -----------------------------------------

    if order.table:

        table = order.table

        table.state = "preparing"

        table.save(update_fields=["state"])

    return created_kots