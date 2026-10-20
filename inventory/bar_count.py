"""
The nightly bar count: what the bottles say against what the till says.

Every peg sold takes its ml off the bottle's stock (a recipe of 30 ml per
peg). A free pour, an over-pour, or a drink sold for cash and never rung up
takes ml out of the bottle but not out of the system, so the system's stock
stays too high, and only counting the bottles shows it. The variance report
(inventory/views.variance_report) can't: it compares the till's deductions
with the recipes, which match by construction.

So at closing a manager counts each bottle: sealed bottles plus the ml left
in the open one. The count is blind (the screen never shows what the system
expects, or the count bends towards it). Rasova records both numbers, shows
the gap in ml, pegs and rupees, and sets the stock to what was counted
(InventoryItem.adjust_stock, "Bar count <date>").

Which bottles: any stock a liquor dish (a dish with a liquor class) uses,
and anything filed under the category "Bar".

Run: python manage.py test inventory.tests.test_bar_count
"""
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.errors import UserError
from core.utils import get_business_date
from inventory.models import BarCount, BarCountLine, InventoryItem, InventoryTransaction, Recipe
from inventory.unit_conversion import IncompatibleUnitsError, convert_quantity

PEG_ML = Decimal("30")
_ML_UNITS = ("ml", "l")


class CountError(UserError):
    """A count Rasova can't take as typed; says what to fix."""


def bar_items(tenant, outlet):
    """The stock to count at the bar, by category then name."""
    liquor_stock = Recipe.objects.filter(
        menu_item__vat_class__isnull=False, inventory_item__outlet=outlet,
    ).values("inventory_item_id")
    return (
        InventoryItem.objects.filter(tenant=tenant, outlet=outlet)
        .filter(Q(id__in=liquor_stock) | Q(category__iexact="bar"))
        .order_by("category", "name")
    )


def sell_prices(items):
    """{item id: the lowest price one unit of it sells for}. A bottle sold
    as a 30 ml peg at Rs 300 and in a Rs 450 cocktail at 60 ml is Rs 7.50 a
    ml, the cheaper of the two, so a gap is never priced high."""
    prices = {}
    recipes = Recipe.objects.filter(inventory_item__in=list(items)).select_related("menu_item", "inventory_item")
    for recipe in recipes:
        try:
            qty = convert_quantity(Decimal(str(recipe.quantity_required)), recipe.unit, recipe.inventory_item.unit)
        except IncompatibleUnitsError:
            continue
        if qty <= 0 or not recipe.menu_item.price:
            continue
        price = Decimal(recipe.menu_item.price) / qty
        key = recipe.inventory_item_id
        prices[key] = min(prices.get(key, price), price)
    return prices


def _number(raw, what, whole=False):
    raw = "" if raw is None else str(raw).strip()
    if raw == "":
        return None
    try:
        value = Decimal(raw)
    except Exception:
        raise CountError(f"{what} must be a number.")
    if not value.is_finite() or value < 0:
        raise CountError(f"{what} can't be below zero.")
    if whole and value != value.to_integral_value():
        raise CountError(f"{what} must be a whole number.")
    return value


def counted_quantity(item, bottles, amount):
    """
    What was counted, in the item's own unit, or None if nothing was typed.
    bottles: sealed bottles (pieces, for stock kept in pieces); amount: ml
    left in the open bottle, or the whole amount in the item's unit for
    stock not kept in ml.
    """
    bottles = _number(bottles, f"Sealed bottles of {item.name}", whole=True)
    amount = _number(amount, f"The open amount of {item.name}")
    if bottles is None and amount is None:
        return None
    bottles, amount = bottles or Decimal("0"), amount or Decimal("0")
    if item.unit == "pcs":
        return bottles + amount
    if item.unit in _ML_UNITS:
        if bottles and not item.bottle_ml:
            raise CountError(f"Set the bottle size of {item.name} first, or type the total ml.")
        ml = bottles * Decimal(item.bottle_ml or 0) + amount
        return convert_quantity(ml, "ml", item.unit)
    return bottles + amount


def save_count(user, outlet, entries):
    """
    Record a count and set the stock to it. entries: {item id: (bottles,
    amount)} as typed; items left blank are not counted. Returns the
    BarCount. CountError (nothing saved) if a figure can't be taken.

    The bar's stock rows are locked in id order, the order every sale locks
    stock in (orders/services/inventory_service.deduct_inventory_for_items),
    so a count and a sale at the same moment can't deadlock: the sale waits
    and then deducts from the counted stock. "Expected" is read under that
    lock. The writes are a fixed number of queries however many bottles.
    """
    with transaction.atomic():
        items = {i.id: i for i in bar_items(outlet.tenant, outlet).order_by("id").select_for_update()}
        prices = sell_prices(items.values())
        counted = []
        for item_id, (bottles, amount) in entries.items():
            item = items.get(item_id)
            if item is None:
                raise CountError("That isn't bar stock at this outlet.")
            quantity = counted_quantity(item, bottles, amount)
            if quantity is not None:
                counted.append((item, quantity))
        if not counted:
            raise CountError("Count at least one bottle.")

        now = timezone.now()
        count = BarCount.objects.create(
            tenant=outlet.tenant, outlet=outlet, business_date=get_business_date(now, outlet), counted_by=user,
        )
        reference = f"Bar count {count.business_date}"
        lines, moves, changed = [], [], []
        for item, quantity in counted:
            lines.append(BarCountLine(
                count=count, item=item, item_name=item.name, unit=item.unit,
                expected=item.stock, counted=quantity, cost_price=item.cost_price,
                sell_price=prices.get(item.id),
            ))
            if quantity != item.stock:
                # The same record InventoryItem.adjust_stock writes.
                moves.append(InventoryTransaction(
                    item=item, tenant_id=item.tenant_id, outlet_id=item.outlet_id,
                    quantity=quantity - item.stock, transaction_type="adjustment", reference=reference,
                ))
                item.stock, item.updated_at = quantity, now
                changed.append(item)
        BarCountLine.objects.bulk_create(lines)
        if changed:
            InventoryItem.objects.bulk_update(changed, ["stock", "updated_at"])
            InventoryTransaction.objects.bulk_create(moves)
    return count


def _to_ml(quantity, unit):
    return convert_quantity(quantity, unit, "ml") if unit in _ML_UNITS else None


def count_report(count):
    """The count's lines with what went missing, biggest loss first, and
    the totals. Missing is what the system expected minus what was counted;
    a negative figure is stock found."""
    rows = []
    for line in count.lines.all():
        missing = line.missing
        missing_ml = _to_ml(missing, line.unit)
        at_cost = (missing * line.cost_price).quantize(Decimal("0.01"))
        at_price = (missing * line.sell_price).quantize(Decimal("0.01")) if line.sell_price is not None else None
        rows.append({
            "name": line.item_name,
            "unit": line.unit,
            "expected": line.expected,
            "counted": line.counted,
            "missing": missing,
            "missing_ml": missing_ml,
            "missing_pegs": (missing_ml / PEG_ML).quantize(Decimal("0.1")) if missing_ml is not None else None,
            "at_cost": at_cost,
            "at_price": at_price,
        })
    rows.sort(key=lambda r: (r["at_price"] if r["at_price"] is not None else r["at_cost"]), reverse=True)

    def total(key, only_missing=True):
        values = [r[key] for r in rows if r[key] is not None and (r["missing"] > 0 or not only_missing)]
        return sum(values, Decimal("0"))

    return {
        "count": count,
        "rows": rows,
        "lines": len(rows),
        "short_lines": sum(1 for r in rows if r["missing"] > 0),
        "missing_pegs": total("missing_pegs"),
        "missing_at_price": total("at_price"),
        "missing_at_cost": total("at_cost"),
        "net_at_cost": total("at_cost", only_missing=False),
        "unpriced": [r["name"] for r in rows if r["at_price"] is None and r["missing"] > 0],
    }
