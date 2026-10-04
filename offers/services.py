# offers/services.py
"""
Runs the offer engine (offers/engine.py) on a real bill and writes the
answer onto its lines. Order.recalculate_totals() calls apply_offers() every
time a bill is totalled, so adding a drink, voiding one, a guest's QR order
or a table merge all re-check the offers with nothing else to remember.

Which lines can take an offer here (engine rule 1):
  * not a free (complimentary) dish, and not one with its own dish discount:
    staff already gave it something, and the two never stack;
  * not liquor on a parcel or takeaway order: liquor can't be sold as a
    parcel in most states, so no offer should invite it;
  * nothing at all on an order through Zomato or Swiggy: the app sets the
    price there;
  * only for a restaurant with the "offers" feature (on by default for pubs).
"""
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from offers.engine import CartLine, Rule, Window, evaluate

ZERO = Decimal("0.00")
FEATURE = "offers"


def _cutoff_hour(outlet):
    from core.utils import _cutoff_hour as cutoff
    return cutoff(outlet)


def _local(moment):
    return timezone.localtime(moment) if moment else None


def rule_from(offer):
    """The engine's Rule for an Offer (its targets and windows prefetched)."""
    stops = [moment for moment in (offer.paused_at, offer.archived_at) if moment]
    targets = list(offer.targets.all())
    return Rule(
        id=offer.id, name=offer.name, kind=offer.kind,
        buy=offer.buy_qty or 0, free=offer.free_qty or 0,
        percent=offer.percent or ZERO, amount=offer.amount or ZERO,
        priority=offer.priority,
        dishes=frozenset(t.menu_item_id for t in targets if t.menu_item_id),
        categories=frozenset(t.category_id for t in targets if t.category_id),
        windows=tuple(Window(days=w.day_set, start=w.start_time, end=w.end_time) for w in offer.windows.all()),
        valid_from=offer.valid_from, valid_until=offer.valid_until,
        live_until=_local(min(stops)) if stops else None,
    )


def rules_for(tenant, outlet):
    """The rules an outlet's bills can use: active offers, and switched-off
    ones (they still cover dishes added before they were switched off)."""
    from offers.models import Offer
    offers = (
        Offer.objects.for_tenant(tenant)
        .filter(Q(outlet=outlet) | Q(outlet__isnull=True))
        .filter(Q(is_active=True) | Q(paused_at__isnull=False))
        .prefetch_related("targets", "windows")
    )
    return [rule_from(offer) for offer in offers]


def offers_apply(order):
    from core.features import has_feature
    if order.source in order.OPERATOR_SOURCES:
        return False
    return has_feature(order.tenant, FEATURE)


def _eligible(order, item):
    if item.is_complimentary or (item.item_discount_pct or 0) > 0:
        return False
    if item.tax_kind == "vat" and (item.is_takeaway or order.source == "takeaway"):
        return False
    return True


def apply_offers(order, items):
    """Work out the offers on a bill's live (not voided) lines, write them on
    the lines, and return the bill's offer total. Lines the offers no longer
    reach are cleared. Runs inside the caller's transaction and row lock."""
    from orders.models import OrderItem

    items = list(items)
    result = {}
    if offers_apply(order):
        clear_voided(order)
        rules = rules_for(order.tenant, order.outlet) if items else []
        if rules:
            from menu.models import MenuItem
            categories = dict(
                MenuItem.objects.filter(id__in={i.menu_item_id for i in items})
                .values_list("id", "category_id")
            )
            fallback = _local(order.created_at)
            lines = [
                CartLine(
                    key=item.pk, position=item.pk, dish_id=item.menu_item_id,
                    category_id=categories.get(item.menu_item_id),
                    unit_price=Decimal(item.price), quantity=item.quantity,
                    added_at=_local(item.added_at) or fallback,
                    eligible=_eligible(order, item),
                )
                for item in items
            ]
            result = evaluate(lines, rules, cutoff_hour=_cutoff_hour(order.outlet))

    changed = []
    for item in items:
        applied = result.get(item.pk)
        wanted = (applied.offer_id, applied.offer_name, applied.discount) if applied else (None, "", ZERO)
        if (item.offer_id, item.offer_name, Decimal(item.offer_discount or 0)) != wanted:
            item.offer_id, item.offer_name, item.offer_discount = wanted
            changed.append(item)
    if changed:
        OrderItem.objects.bulk_update(changed, ["offer", "offer_name", "offer_discount"])
    return sum((Decimal(item.offer_discount or 0) for item in items), ZERO)


def clear_voided(order):
    """A voided line keeps no offer, so offer figures only ever describe what
    the guest was billed for."""
    order.items.filter(status="voided").exclude(offer_discount=0).update(
        offer=None, offer_name="", offer_discount=ZERO)


def rule_json(rule):
    """A Rule in the form static/js/offers.js reads (moments as the outlet's
    local wall time, "YYYY-MM-DDTHH:MM:SS")."""
    def moment(value):
        return value.strftime("%Y-%m-%dT%H:%M:%S") if value else None
    return {
        "id": rule.id, "name": rule.name, "kind": rule.kind, "buy": rule.buy, "free": rule.free,
        "percent": str(rule.percent), "amount": str(rule.amount), "priority": rule.priority,
        "dishes": sorted(rule.dishes), "categories": sorted(rule.categories),
        "windows": [{"days": sorted(w.days),
                     "start": w.start.strftime("%H:%M") if w.start else None,
                     "end": w.end.strftime("%H:%M") if w.end else None} for w in rule.windows],
        "validFrom": rule.valid_from.isoformat() if rule.valid_from else None,
        "validUntil": rule.valid_until.isoformat() if rule.valid_until else None,
        "liveUntil": moment(rule.live_until),
    }


def page_offers(tenant, outlet):
    """What a cart needs to show offers before the order exists, for a page to
    embed with json_script and static/js/offers.js to read: the offers a new
    line can take, every dish's category and own price (modifiers are never
    discounted), the business day's start and the outlet's UTC offset. None
    when the restaurant has no offers, so the page loads nothing extra."""
    from core.features import has_feature
    if not tenant or not has_feature(tenant, FEATURE):
        return None
    rules = [rule for rule in rules_for(tenant, outlet) if rule.live_until is None]
    if not rules:
        return None
    from menu.models import MenuItem
    dishes = {
        str(dish_id): {"category": category_id, "price": str(price)}
        for dish_id, category_id, price in
        MenuItem.objects.filter(tenant=tenant, outlet=outlet).values_list("id", "category_id", "price")
    }
    offset = timezone.localtime().utcoffset()
    return {
        "rules": [rule_json(rule) for rule in rules],
        "dishes": dishes,
        "cutoffHour": _cutoff_hour(outlet),
        "utcOffset": int(offset.total_seconds() // 60) if offset else 0,
    }
