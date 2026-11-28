"""
A Friday night already under way at the demo pub (P3, 14 Nov 2026).

A stranger opening the demo pub should find a believable night, not an empty
bar: four tables drinking, eight bills already paid tonight, the register
open, the lager keg tapped at the start of the night and nearly dry (the keg
board shows it, and the stock alert has gone off), and tonight's report with
real numbers. Then they can take an order, print a bill and look at the
report themselves, without anyone in the room.

The night is built the way service builds it: the dishes are sent, which
takes their recipes off the stock (orders.services.inventory_service), so
the keg board, the low-stock alert and the bar count all read real rows.
Running it again starts the night over.

Used by `python manage.py seed_pub_demo` (devtools/management/commands).
"""
import random
from datetime import timedelta
from decimal import Decimal as D

from django.utils import timezone

from core.utils import get_business_date, get_business_date_range

from inventory.models import InventoryItem, InventoryTransaction, KegChange
from menu.models import MenuItem
from notifications.models import Notification
from orders.models import Order, OrderItem, Payment, Table
from orders.services.demo_seed import _clear_transactional_data
from orders.services.inventory_service import deduct_inventory_for_items
from orders.services.tax_service import tax_snapshot_for
from shifts.models import CashSession

KEG = "Lager draught"
KEG_ML = D("30000")
LOW_AT_ML = D("6000")         # the alert goes off with a fifth of a keg left
OPEN_FLOAT = D("3000")

# Each table's round: (dish, quantity). Fixed, so every reset tells the same story.
PAID = [
    ("T1", [("Lager pitcher", 2), ("Masala Peanuts", 1)], "upi"),
    ("T3", [("Old Monk 60 ml", 4), ("Chilli Chicken", 2)], "cash"),
    ("T6", [("Lager pint", 8), ("Loaded Nachos", 1)], "card"),
    ("T7", [("Jameson 30 ml", 3), ("Rum and Coke", 2)], "upi"),
    ("T9", [("Lager pitcher", 3), ("Chilli Chicken", 1)], "cash"),
    ("T10", [("Lager pint", 4), ("Old Monk 30 ml", 2), ("Masala Peanuts", 2)], "upi"),
    ("T1", [("Lager pitcher", 1), ("Lager pint", 2)], "card"),
    ("T3", [("Rum and Coke", 3), ("Loaded Nachos", 1)], "upi"),
]
OPEN = [
    ("T2", [("Lager pitcher", 2), ("Loaded Nachos", 1)]),
    ("T4", [("Old Monk 60 ml", 2), ("Lager pint", 3)]),
    ("T5", [("Jameson 30 ml", 2), ("Chilli Chicken", 1)]),
    ("T8", [("Lager pint", 4), ("Masala Peanuts", 2)]),
]


def _order(tenant, outlet, table, lines, by, when):
    """An order at `table`, its dishes sent (so their recipes leave the stock)."""
    order = Order.objects.create(tenant=tenant, outlet=outlet, table=table, created_by=by,
                                 status="open", source="dine_in")
    items = []
    for name, quantity in lines:
        dish = MenuItem.objects.get(outlet=outlet, name=name)
        # The tax as the till copies it when a dish is ordered: liquor at VAT
        items.append(OrderItem.objects.create(
            order=order, menu_item=dish, quantity=quantity, price=dish.price,
            total_price=dish.price * quantity, status="served", **tax_snapshot_for(dish, tenant)))
    deduct_inventory_for_items(items)
    order.recalculate_totals()
    Order.objects.filter(pk=order.pk).update(created_at=when)
    return order


def start_the_night(tenant, outlet, staff, now=None):
    """Clear the pub's last night and play tonight up to now. Returns a summary."""
    now = now or timezone.now()
    rng = random.Random(20261114)
    _clear_transactional_data(tenant)
    InventoryTransaction.objects.filter(item__outlet=outlet).delete()
    KegChange.objects.filter(outlet=outlet).delete()
    Notification.objects.filter(tenant=tenant).delete()
    CashSession.objects.filter(tenant=tenant, outlet=outlet).delete()

    # Tonight is the last three hours, but never from before today's business
    # day began (6 am unless the outlet says otherwise): a night across that
    # line split its takings between two days, and today's report showed less
    # than the pub took (test_pub_night failed every morning from 6 to about
    # 9, 28 Nov 2026). Every moment below is a minute of a three-hour night,
    # fitted to tonight's real length and never after now.
    day_began, _ = get_business_date_range(get_business_date(now, outlet), outlet)
    start = max(now - timedelta(hours=3), day_began)
    scale = (now - start) / timedelta(hours=3)

    def at(minute):
        return min(start + timedelta(minutes=minute) * scale, now)

    session = CashSession.objects.create(tenant=tenant, outlet=outlet, opened_by=staff["manager"],
                                         opening_balance=OPEN_FLOAT, status="open")
    CashSession.objects.filter(pk=session.pk).update(opened_at=start)

    # A fresh keg went on at the start of the night.
    keg = InventoryItem.objects.get(outlet=outlet, name=KEG)
    keg.stock = KEG_ML
    keg.low_stock_threshold = LOW_AT_ML
    keg.save(update_fields=["stock", "low_stock_threshold"])
    change = KegChange.objects.create(tenant=tenant, outlet=outlet, item=keg, keg_size=KEG_ML,
                                      changed_by=staff["manager"])
    KegChange.objects.filter(pk=change.pk).update(changed_at=start)

    tables = {t.name: t for t in Table.objects.filter(outlet=outlet)}
    paid_total = D("0")
    for n, (table, lines, method) in enumerate(PAID):
        minute = 15 + n * 18 + rng.randint(0, 8)
        order = _order(tenant, outlet, tables[table], lines, staff["captain"], at(minute))
        payment = Payment.objects.create(order=order, method=method, amount=order.grand_total,
                                         created_by=staff["cashier"])
        Order.objects.filter(pk=order.pk).update(status="closed", closed_at=at(minute + 40))
        Payment.objects.filter(pk=payment.pk).update(paid_at=at(minute + 40))
        paid_total += order.grand_total
    for n, (table, lines) in enumerate(OPEN):
        # 50, 40, 30... minutes before the end of the night
        _order(tenant, outlet, tables[table], lines, staff["captain"], at(130 + n * 10))

    keg.refresh_from_db()
    return {"paid": len(PAID), "open": len(OPEN), "takings": paid_total, "keg_left_ml": keg.stock}
