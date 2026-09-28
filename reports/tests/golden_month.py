"""
A fixed month of trading for the golden report tests: September 2026 at
three outlets (GST extra, GST included, composition), seeded so it comes out
the same every time.

It deliberately includes the awkward cases reports get wrong: bills after
midnight (they belong to the business day before), bills just outside the
month, cancelled and unpaid orders, voided and free dishes, item and order
discounts, parcel charges, split payments and a refund.

Don't change the generator casually: reports/tests/golden/reports_v1.json was
produced from it. If it truly has to change, regenerate that file in the same
commit and say why.
"""
import datetime as dt
import random
import zoneinfo
from decimal import Decimal

from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Payment
from tenants.models import Outlet, Tenant

SEED = 20260930
FIRST_DAY = dt.date(2026, 9, 1)
LAST_DAY = dt.date(2026, 9, 30)
ORDERS_PER_OUTLET = 40
IST = zoneinfo.ZoneInfo("Asia/Kolkata")

# (name, gst_inclusive, is_composition_scheme)
OUTLETS = [
    ("Exclusive", False, False),
    ("Inclusive", True, False),
    ("Composition", False, True),
]

# (name, category, price, gst %, veg)
MENU = [
    ("Masala Dosa", "Mains", "90.00", "5", True),
    ("Ghee Roast Dosa", "Mains", "140.00", "5", True),
    ("Plain Curd", "Mains", "30.00", "0", True),
    ("Chicken 65", "Starters", "240.00", "5", False),
    ("Gobi Manchurian", "Starters", "180.00", "5", True),
    ("Veg Club Sandwich", "Starters", "99.99", "18", True),
    ("Filter Coffee", "Beverages", "25.00", "5", True),
    ("Fresh Lime Soda", "Beverages", "60.00", "5", True),
    ("Bottled Water 1L", "Beverages", "20.00", "12", True),
    ("Kingfisher Premium 650ml", "Bar", "320.00", "18", True),
    ("Old Monk 60ml", "Bar", "150.00", "18", True),
]

# Hours bills are opened at; 0 and 1 are after midnight.
HOURS = [12, 13, 13, 14, 19, 20, 20, 21, 21, 22, 23, 0, 1]

# (business day, hour, minute): hand-placed bills around the month's edges
EDGE_BILLS = [
    (dt.date(2026, 8, 31), 23, 10),   # the day before the month: outside
    (dt.date(2026, 8, 31), 1, 40),    # Sep 1 at 1:40 AM, still August's business day
    (FIRST_DAY, 6, 5),                # the first minutes of Sep 1's business day
    (LAST_DAY, 1, 15),                # Oct 1 at 1:15 AM, still Sep 30's business day
    (dt.date(2026, 10, 1), 12, 0),    # the day after the month: outside
]


def _moment(business_day, hour, minute):
    """A bill opened at hour:minute of a business day (which runs 6 AM to 6 AM)."""
    day = business_day + dt.timedelta(days=1) if hour < 6 else business_day
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def build_month():
    """Create the month. Returns (tenant, [outlets])."""
    rng = random.Random(SEED)
    tenant = Tenant.objects.create(name="Golden Month")
    outlets = []
    for index, (name, inclusive, composition) in enumerate(OUTLETS):
        outlet = Outlet.objects.create(
            tenant=tenant, name=name, gst_no="29ABCDE1234F1Z5",
            gst_inclusive=inclusive, is_composition_scheme=composition,
        )
        dishes = _menu(tenant, outlet)
        moments = [(_moment(*edge), True) for edge in EDGE_BILLS]
        while len(moments) < ORDERS_PER_OUTLET:
            day = FIRST_DAY + dt.timedelta(days=rng.randrange(30))
            moments.append((_moment(day, rng.choice(HOURS), rng.randrange(60)), False))
        for number, (opened_at, edge) in enumerate(moments):
            _bill(rng, tenant, outlet, dishes, f"GM-{index}{number:03d}", opened_at, edge)
        outlets.append(outlet)
    return tenant, outlets


def _menu(tenant, outlet):
    categories, dishes = {}, []
    for name, category, price, gst, veg in MENU:
        if category not in categories:
            categories[category] = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name=category)
        dishes.append(MenuItem.objects.create(
            tenant=tenant, outlet=outlet, category=categories[category], name=name,
            price=Decimal(price), gst_percentage=Decimal(gst), is_veg=veg,
        ))
    return dishes


def _bill(rng, tenant, outlet, dishes, number, opened_at, edge):
    roll = rng.random()
    status = "paid" if edge or roll < 0.80 else "closed" if roll < 0.88 else "cancelled" if roll < 0.95 else "open"
    dtype, dval = None, Decimal("0")
    roll = rng.random()
    if roll < 0.08:
        dtype, dval = "percentage", Decimal(rng.choice(["10", "15"]))
    elif roll < 0.15:
        dtype, dval = "amount", Decimal(rng.choice(["50", "100"]))
    parcel = Decimal(rng.choice(["10", "20", "30"])) if rng.random() < 0.2 else Decimal("0")
    order = Order.objects.create(
        tenant=tenant, outlet=outlet, status="open" if status in ("paid", "closed") else status,
        order_number=number, source="dine_in",
        discount_type=dtype, discount_value=dval, parcel_surcharge=parcel,
        # as toggle_parcel does: the outlet's parcel GST rate, copied onto the bill
        parcel_gst_rate=outlet.parcel_gst_rate if parcel else None,
    )
    for dish in rng.sample(dishes, rng.randint(1, 5)):
        qty = rng.randint(1, 4)
        roll = rng.random()
        OrderItem.objects.create(
            order=order, menu_item=dish, quantity=qty, price=dish.price,
            gst_percentage=dish.gst_percentage, total_price=dish.price * qty,
            status="voided" if roll < 0.05 else "served",
            is_complimentary=0.05 <= roll < 0.09,
            item_discount_pct=Decimal(rng.choice(["10", "25"])) if 0.09 <= roll < 0.17 else Decimal("0"),
        )
    order.recalculate_totals()
    # Opened on its day, then settled: the bill number's year comes from the
    # day the order was opened, so the month never depends on today.
    order.created_at = opened_at
    Order.objects.filter(pk=order.pk).update(created_at=opened_at)
    if status in ("paid", "closed"):
        order.status = status
        order.save(update_fields=["status"])
        _pay(rng, order, opened_at + dt.timedelta(minutes=45))


def _pay(rng, order, paid_at):
    grand = order.grand_total
    if rng.random() < 0.3:
        cash = (grand / 2).quantize(Decimal("1"))
        rows = [("cash", cash), ("upi", grand - cash)]
    else:
        rows = [(rng.choice(["cash", "upi", "card"]), grand)]
    if rng.random() < 0.04:
        rows.append(("refund", -(grand / 4).quantize(Decimal("1"))))
    for method, amount in rows:
        payment = Payment.objects.create(order=order, method=method, amount=amount)
        Payment.objects.filter(pk=payment.pk).update(paid_at=paid_at)
