"""
Draught beer by the keg: what each keg gave, and what it lost.

A keg item is bar stock in ml or litres whose container (bottle_ml) is at
least KEG_MIN_ML: kegs are 20 to 50 litres, bottles 2 litres at most. Each
pint sold takes its ml off the item's stock, as any recipe does.

When a keg runs dry the bartender presses "Change keg". Rasova adds up what
the till sold from it (the "consume" rows since the previous change, net of
cancels) and compares that with what a keg holds. The difference, foam,
spillage, or beer that left without a bill, is taken off the stock then,
so the stock stays true, and recorded (KegChange.lost). The first change of
an item records nothing more: Rasova can't know when that keg was tapped.

Run: python manage.py test inventory.tests.test_kegs
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from core.errors import UserError
from inventory.bar_count import bar_items, sell_prices
from inventory.models import InventoryTransaction, KegChange
from inventory.unit_conversion import convert_quantity

KEG_MIN_ML = 5000


class KegError(UserError):
    """A keg change Rasova can't make; says why."""


def keg_items(tenant, outlet):
    return bar_items(tenant, outlet).filter(unit__in=("ml", "l"), bottle_ml__gte=KEG_MIN_ML)


def keg_size(item):
    return convert_quantity(Decimal(item.bottle_ml), "ml", item.unit)


def sold_since(item, since):
    """What the till sold of `item` after `since` (all time if None), net
    of cancels, in the item's unit."""
    rows = InventoryTransaction.objects.filter(item=item, transaction_type="consume")
    if since is not None:
        rows = rows.filter(created_at__gt=since)
    return -(rows.aggregate(total=Sum("quantity"))["total"] or Decimal("0"))


# A second press within this long is a double tap, not a keg drunk dry in
# seconds: it would otherwise record a whole keg as lost.
DOUBLE_PRESS = timedelta(minutes=2)


def change_keg(user, outlet, item_id):
    """
    The keg on tap is finished; a new one is tapped. Locks the keg item's
    row first (a sale deducting from it waits, then counts against the new
    keg), so two presses at once are taken one after the other, and the
    second, seeing the first a moment ago, is refused.
    """
    with transaction.atomic():
        item = keg_items(outlet.tenant, outlet).select_for_update().filter(id=item_id).first()
        if item is None:
            raise KegError("That isn't a keg at this outlet. Set its keg size (in ml) on the inventory board.")
        last = KegChange.objects.filter(item=item).order_by("-changed_at", "-id").first()
        now = timezone.now()
        if last is not None and now - last.changed_at < DOUBLE_PRESS:
            raise KegError(f"The {item.name} keg was changed a moment ago. Change it again only when it runs dry.")
        size = keg_size(item)
        sold = lost = None
        if last is not None:
            sold = sold_since(item, last.changed_at)
            lost = max(size - sold, Decimal("0"))
            take = min(lost, item.stock)
            if take > 0:
                item.adjust_stock(-take, f"Keg change: {take} {item.unit} of {item.name} not sold from the last keg")
        return KegChange.objects.create(
            tenant=outlet.tenant, outlet=outlet, item=item, changed_by=user,
            keg_size=size, sold=sold, lost=lost,
        )


def keg_board(tenant, outlet):
    """Each keg now (sold from the one on tap so far), and the past changes
    with each finished keg's yield and loss, newest first. A fixed number of
    queries however many kegs: the last change of every keg in one, and what
    each sold since its own last change in one more (one filtered sum per
    keg)."""
    items = list(keg_items(tenant, outlet))
    prices = sell_prices(items)
    last_change = {}
    for change in (KegChange.objects.filter(item__in=items)
                   .order_by("item_id", "-changed_at", "-id").values("item_id", "changed_at")):
        last_change.setdefault(change["item_id"], change["changed_at"])
    sums = {}
    if last_change:
        sums = InventoryTransaction.objects.filter(
            transaction_type="consume", item_id__in=list(last_change),
        ).aggregate(**{
            f"item_{item_id}": Sum("quantity", filter=Q(item_id=item_id, created_at__gt=since))
            for item_id, since in last_change.items()
        })
    now = []
    for item in items:
        size = keg_size(item)
        since = last_change.get(item.id)
        sold = -(sums.get(f"item_{item.id}") or Decimal("0")) if since is not None else None
        now.append({
            "item": item, "size": size, "last_change": since,
            "sold": sold, "share": (sold / size * 100).quantize(Decimal("1")) if sold is not None and size else None,
        })
    history = []
    changes = (KegChange.objects.filter(tenant=tenant, outlet=outlet)
               .select_related("item", "changed_by").order_by("-changed_at", "-id")[:50])
    for change in changes:
        price = prices.get(change.item_id)
        history.append({
            "change": change,
            "yield": (change.sold / change.keg_size * 100).quantize(Decimal("1"))
            if change.sold is not None and change.keg_size else None,
            "lost_value": (change.lost * price).quantize(Decimal("0.01"))
            if change.lost is not None and price is not None else None,
            "over_a_keg": change.sold is not None and change.sold > change.keg_size,
        })
    return {"now": now, "history": history}
