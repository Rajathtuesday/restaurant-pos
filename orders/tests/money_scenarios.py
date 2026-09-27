"""
Fixed bill scenarios for the money safety net.

generate_specs() returns the same list every time for the same SEED, so the
golden files in orders/tests/golden/ can be checked after any change to how
bills are totalled. Don't change the generator casually: every golden file
was produced from it, and changing it changes every scenario. If it truly has
to change, regenerate the golden files in the same commit and say why.

A spec is plain data (strings and ints), so it can be turned into real
Order/OrderItem rows (create_orders) or fed straight into the frozen copy of
the maths in legacy_totals.py.
"""
import random
from decimal import Decimal

from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from tenants.models import Outlet, Tenant

SEED = 20260927
COUNT = 5000

# (gst_inclusive, is_composition_scheme) -- every mode recalculate_totals has
OUTLET_CONFIGS = [(False, False), (True, False), (False, True), (True, True)]

GST_RATES = ["0", "5", "12", "18", "28"]
QTYS = [1, 1, 1, 1, 2, 2, 3, 4, 5, 7, 10, 13, 20]
ITEM_DISCOUNTS = ["0"] * 10 + ["5", "10", "12.50", "33.33", "50", "100"]
PCT_DISCOUNTS = ["5", "10", "12.50", "15", "33.33", "50", "100"]
AMT_DISCOUNTS = ["1", "10", "49.99", "100", "250", "999.99", "100000"]
PARCELS = ["5", "10", "15", "37.50", "120"]
MODIFIERS = ["10", "25", "49.50", "100"]
LIVE_STATUSES = ["pending", "sent", "served"]


def make_item(price, qty=1, gst="5", disc="0", status="pending", comp=False, modifier="0"):
    price = Decimal(price)
    return {
        "price": f"{price:.2f}",
        "qty": qty,
        "total": f"{(price + Decimal(modifier)) * qty:.2f}",
        "gst": gst,
        "disc": disc,
        "status": status,
        "comp": comp,
    }


def make_spec(cfg, items, dtype=None, dval="0", parcel="0"):
    return {"cfg": cfg, "items": items, "dtype": dtype, "dval": dval, "parcel": parcel}


def edge_specs():
    """Hand-picked awkward bills, in every outlet mode."""
    out = []
    for cfg in range(len(OUTLET_CONFIGS)):
        out += [
            make_spec(cfg, []),                                                # no items at all
            make_spec(cfg, [make_item("100", status="voided")]),               # everything voided
            make_spec(cfg, [make_item("100", comp=True)]),                     # everything complimentary
            make_spec(cfg, [make_item("0")]),                                  # a free dish
            make_spec(cfg, [make_item("0.01")]),                               # one paisa
            make_spec(cfg, [make_item("0.10")]),                               # tax of exactly half a paisa
            make_spec(cfg, [make_item("25")]),                                 # the classic inclusive coffee
            make_spec(cfg, [make_item("99.99", qty=3, gst="18")]),
            make_spec(cfg, [make_item("100", disc="100")]),                    # 100% item discount
            make_spec(cfg, [make_item("100"), make_item("50", gst="0")], "percentage", "100"),  # 100% order discount
            make_spec(cfg, [make_item("100")], "amount", "100000"),            # discount far bigger than the bill
            make_spec(cfg, [make_item("100", status="voided")], parcel="15"),  # parcel charge only
            make_spec(cfg, [make_item("40", qty=3)], parcel="15"),             # parcel on a normal order
            make_spec(cfg, [make_item("333.33", qty=3, gst="12"), make_item("0.05", gst="28")], "amount", "0.01"),
            make_spec(cfg, [make_item("2500", qty=20, gst="28", modifier="100")], "percentage", "33.33", "120"),
        ]
    return out


def _price(rng):
    roll = rng.random()
    if roll < 0.05:
        return "0.00"
    if roll < 0.60:
        return f"{rng.randint(1, 60) * 5}.00"          # usual round menu prices
    return f"{rng.randint(1, 250000) / 100:.2f}"      # any amount with paise


def generate_specs(count=COUNT, seed=SEED):
    rng = random.Random(seed)
    specs = edge_specs()
    while len(specs) < count:
        cfg = len(specs) % len(OUTLET_CONFIGS)
        items = []
        for _ in range(rng.randint(1, 8)):
            price = _price(rng)
            qty = rng.choice(QTYS)
            modifier = rng.choice(MODIFIERS) if rng.random() < 0.15 else "0"
            items.append(make_item(
                price, qty=qty, gst=rng.choice(GST_RATES), disc=rng.choice(ITEM_DISCOUNTS),
                status="voided" if rng.random() < 0.07 else rng.choice(LIVE_STATUSES),
                comp=rng.random() < 0.05, modifier=modifier,
            ))
        roll = rng.random()
        if roll < 0.55:
            dtype, dval = None, "0"
        elif roll < 0.80:
            dtype, dval = "percentage", rng.choice(PCT_DISCOUNTS)
        else:
            dtype, dval = "amount", rng.choice(AMT_DISCOUNTS)
        parcel = "0" if rng.random() < 0.7 else rng.choice(PARCELS)
        specs.append(make_spec(cfg, items, dtype, dval, parcel))
    return specs[:count]


# ---------------------------------------------------------------------------
# Building real rows
# ---------------------------------------------------------------------------

def build_world(name="Money Safety Net"):
    """One tenant with an outlet (and a dish) for every outlet mode."""
    tenant = Tenant.objects.create(name=name)
    outlets, dishes = [], []
    for i, (inclusive, composition) in enumerate(OUTLET_CONFIGS):
        outlet = Outlet.objects.create(
            tenant=tenant, name=f"Mode {i}",
            gst_inclusive=inclusive, is_composition_scheme=composition,
        )
        category = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Everything")
        dish = MenuItem.objects.create(
            tenant=tenant, outlet=outlet, category=category, name="Dish", price=Decimal("1"),
        )
        outlets.append(outlet)
        dishes.append(dish)
    return {"tenant": tenant, "outlets": outlets, "dishes": dishes}


def create_orders(world, specs):
    """Real Order and OrderItem rows for each spec, in the same order.

    bulk_create keeps this fast for thousands of bills; totals are then
    computed by calling the real recalculate_totals() on each order.
    """
    tenant = world["tenant"]
    orders = Order.objects.bulk_create([
        Order(
            tenant=tenant, outlet=world["outlets"][spec["cfg"]], status="open",
            discount_type=spec["dtype"], discount_value=Decimal(spec["dval"]),
            parcel_surcharge=Decimal(spec["parcel"]),
        )
        for spec in specs
    ], batch_size=1000)
    rows = []
    for order, spec in zip(orders, specs):
        dish = world["dishes"][spec["cfg"]]
        for it in spec["items"]:
            rows.append(OrderItem(
                order=order, menu_item=dish, quantity=it["qty"], price=Decimal(it["price"]),
                item_discount_pct=Decimal(it["disc"]), gst_percentage=Decimal(it["gst"]),
                total_price=Decimal(it["total"]), status=it["status"], is_complimentary=it["comp"],
            ))
    OrderItem.objects.bulk_create(rows, batch_size=2000)
    return orders


def money(value):
    return f"{Decimal(value):.2f}"


def snapshot(order, index, spec):
    """Everything a bill shows about money, as plain strings."""
    return {
        "i": index,
        "cfg": spec["cfg"],
        "parcel": spec["parcel"],
        "sub": money(order.subtotal),
        "gst": money(order.gst_total),
        "disc": money(order.discount_total),
        "grand": money(order.grand_total),
        "ro": money(order.round_off),
        "cgst": money(order.cgst_total),
        "sgst": money(order.sgst_total),
        "bd": [
            [row["rate"], row["cgst_rate"], row["sgst_rate"], row["cgst_amount"], row["sgst_amount"]]
            for row in (order.gst_breakdown_cache or [])
        ],
    }
