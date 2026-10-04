"""
The offer engine: which units on a bill an offer makes free or cheaper.

Pure Python and Decimal, no database and no Django, so every rule can be
tested on its own; offers/services.py feeds it a bill's lines and the
outlet's offers and writes the answer onto the lines. The carts' browser
copy (static/js/offers.js) follows the same rules and is checked against
this module in CI (offers/tests/test_offers_js.py).

The rules
---------
1. Which units can take an offer. A bill line is quantity x one dish. Its
   units can take an offer unless the line is free (complimentary), has its
   own dish discount (staff gave it something already; the two never
   stack), or the caller marks it ineligible (liquor on a parcel order, an
   order through Zomato or Swiggy). An offer only ever discounts the dish's
   own price: paid modifiers (a mixer with the whisky) are never discounted.

2. When an offer counts. A unit takes an offer only if the offer was live at
   the moment its line was added: the price is locked then. A pitcher
   ordered at 7:55 is a happy-hour pitcher even if the bill comes at 8:20,
   and an offer switched off (paused) mid-meal keeps the units added before
   it was paused. "Live" means: inside the offer's dates and one of its
   windows. Dates follow the business day (6 AM to 6 AM by default), so an
   offer "until Friday" still covers 1 AM on Saturday. A window's times are
   plain clock times, as an owner reads them: 05:00 to 20:00 is 5 AM to 8 PM
   on that day. A window whose end is not after its start runs past
   midnight, and the part after midnight belongs to the day it started:
   Friday 20:00 to 02:00 covers 1 AM on Saturday. A window with days but no
   times is the whole business day. A window with only a start runs to the
   end of the business day; with only an end, from its start.

3. One offer per line. Offers are tried in order: highest priority first,
   then the one worth more to the guest on its own, then the oldest. Each
   takes the units it can from the lines no earlier offer has touched; a
   line one offer has touched is closed to the others. So two cashiers,
   whatever order they tapped the drinks in, always get the same bill.

4. Buy N, get M free: the offer's eligible units, dearest first (ties: the
   line added first, then its first unit), cut into groups of N + M. In
   every full group the M cheapest units are free. A group that isn't full
   gets nothing, and its units stay open to the next offer. So two pitchers
   at 2,500 and 2,300 and a third at 2,300 under "buy 2, get 1": the third
   is free; a fourth pitcher on its own waits for its group.

   Percent off: each eligible unit's price x percent. Amount off: a fixed
   sum off each eligible unit, never more than its price.

5. Money. Each line's discount is the exact sum over its units, rounded once
   to the paisa, half up, and never more than the dish value of the line
   (price x quantity). It comes off the line's value before tax, exactly
   like a dish discount, in the tax engine (orders/services/tax_engine.py).
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

BUY_GET_FREE = "buy_get_free"
PERCENT_OFF = "percent_off"
AMOUNT_OFF = "amount_off"
KINDS = (BUY_GET_FREE, PERCENT_OFF, AMOUNT_OFF)

PAISA = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")


def to_paisa(amount):
    return Decimal(amount).quantize(PAISA, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Window:
    """When an offer runs: business-day weekdays (Monday 0 to Sunday 6; empty
    means every day) and a time range on the clock (None and None: all day).
    An end not after the start runs past midnight."""
    days: frozenset = frozenset()
    start: time | None = None
    end: time | None = None


@dataclass(frozen=True)
class Rule:
    """One offer, as the engine needs it."""
    id: int
    name: str
    kind: str
    buy: int = 0                         # buy_get_free: the N paid units
    free: int = 1                        # buy_get_free: the M free units
    percent: Decimal = ZERO              # percent_off
    amount: Decimal = ZERO               # amount_off, per unit
    priority: int = 0
    dishes: frozenset = frozenset()      # dish ids it covers
    categories: frozenset = frozenset()  # category ids it covers; both empty: the whole menu
    windows: tuple = ()                  # Window; empty: any time
    valid_from: date | None = None       # business dates, both ends included
    valid_until: date | None = None
    live_until: datetime | None = None   # paused or archived at: lines added later don't count


@dataclass(frozen=True)
class CartLine:
    """One bill line, as the engine needs it."""
    key: object                          # the caller's id for the line
    position: int                        # order the lines were added in (ties)
    dish_id: int
    category_id: int | None
    unit_price: Decimal                  # the dish's own price, no modifiers
    quantity: int
    added_at: datetime | None = None     # local time the line was added
    eligible: bool = True                # False: free, own discount, liquor parcel...


@dataclass(frozen=True)
class Applied:
    """What one offer did to one line."""
    offer_id: int
    offer_name: str
    discount: Decimal                    # rupees off the line, to the paisa
    free_units: int = 0                  # units it made free (buy_get_free)


@dataclass
class _Unit:
    line: CartLine
    index: int
    price: Decimal
    discount: Decimal = field(default=ZERO)


def business_moment(moment, cutoff_hour):
    """The business date and the clock time of a local moment."""
    business_date = (moment - timedelta(hours=cutoff_hour)).date()
    return business_date, moment.time()


def in_window(window, moment, cutoff_hour):
    """Whether a local moment is inside a window (module docstring, rule 2).

    Until 4 Oct 2026 the times were counted from the start of the business
    day, so a window starting before 6 AM (05:00 to 20:00) became 5 to 6 AM
    only, and an offer set to run all day never showed on a 10 AM bill."""
    def day_ok(day):
        return not window.days or day.weekday() in window.days

    start, end = window.start, window.end
    if start is None and end is None:
        return day_ok(business_moment(moment, cutoff_hour)[0])
    start = start or time(cutoff_hour, 0)
    end = end or time(cutoff_hour, 0)
    if start == end:                  # open both ends at the cutoff: the whole business day
        return day_ok(business_moment(moment, cutoff_hour)[0])
    clock = moment.time()
    if start < end:
        return start <= clock < end and day_ok(moment.date())
    if clock >= start:                # past midnight: before it, the day itself...
        return day_ok(moment.date())
    if clock < end:                   # ...after it, the day it started
        return day_ok(moment.date() - timedelta(days=1))
    return False


def live_at(rule, moment, cutoff_hour):
    """Whether a rule was live at a local moment (None: no time known, so only
    rules without dates or windows count)."""
    if moment is None:
        return not rule.windows and rule.valid_from is None and rule.valid_until is None
    if rule.live_until is not None and moment >= rule.live_until:
        return False
    business_date, _ = business_moment(moment, cutoff_hour)
    if rule.valid_from and business_date < rule.valid_from:
        return False
    if rule.valid_until and business_date > rule.valid_until:
        return False
    if rule.windows and not any(in_window(w, moment, cutoff_hour) for w in rule.windows):
        return False
    return True


def _covers(rule, line):
    if not rule.dishes and not rule.categories:
        return True
    return line.dish_id in rule.dishes or (line.category_id is not None and line.category_id in rule.categories)


def _units(lines):
    units = []
    for line in lines:
        for index in range(max(int(line.quantity), 0)):
            units.append(_Unit(line=line, index=index, price=Decimal(line.unit_price)))
    return units


def _run(rule, units):
    """Apply one rule to the units it may take. Returns the units it took
    (their discount set) and leaves the others untouched."""
    taken = []
    if rule.kind == BUY_GET_FREE:
        size = rule.buy + rule.free
        if rule.buy < 1 or rule.free < 1:
            return []
        ordered = sorted(units, key=lambda u: (-u.price, u.line.position, u.index))
        for start in range(0, len(ordered) - size + 1, size):
            group = ordered[start:start + size]
            for unit in group[:rule.buy]:
                unit.discount = ZERO
            for unit in group[rule.buy:]:
                unit.discount = unit.price
            taken.extend(group)
    elif rule.kind == PERCENT_OFF:
        if rule.percent <= 0:
            return []
        pct = min(Decimal(rule.percent), HUNDRED)
        for unit in units:
            unit.discount = unit.price * pct / HUNDRED
            taken.append(unit)
    elif rule.kind == AMOUNT_OFF:
        if rule.amount <= 0:
            return []
        for unit in units:
            unit.discount = min(Decimal(rule.amount), unit.price)
            taken.append(unit)
    return taken


def _worth(rule, units):
    """What a rule would take off on its own (for ordering, rule 3)."""
    copies = [_Unit(line=u.line, index=u.index, price=u.price) for u in units]
    return sum((u.discount for u in _run(rule, copies)), ZERO)


def evaluate(lines, rules, *, cutoff_hour=6):
    """{line key: Applied} for every line an offer touches. `lines` are
    CartLine, `rules` are Rule; added_at moments must already be local time.
    The answer depends only on the lines and rules, never on the order the
    lists come in."""
    lines = sorted(lines, key=lambda line: line.position)
    open_lines = [line for line in lines if line.eligible and line.quantity > 0 and line.unit_price > 0]
    all_units = _units(open_lines)

    def candidates(rule, closed):
        return [u for u in all_units
                if u.line.key not in closed
                and _covers(rule, u.line)
                and live_at(rule, u.line.added_at, cutoff_hour)]

    ranked = sorted(
        (r for r in rules if r.kind in KINDS),
        key=lambda r: (-r.priority, -_worth(r, candidates(r, set())), r.id),
    )

    closed, result = set(), {}
    for rule in ranked:
        units = candidates(rule, closed)
        for unit in units:
            unit.discount = ZERO
        taken = _run(rule, units)
        by_line = {}
        for unit in taken:
            by_line.setdefault(unit.line.key, []).append(unit)
        for key, line_units in by_line.items():
            line = line_units[0].line
            discount = to_paisa(sum((u.discount for u in line_units), ZERO))
            discount = min(discount, to_paisa(Decimal(line.unit_price) * line.quantity))
            closed.add(key)
            if discount > 0:
                free_units = sum(1 for u in line_units if u.discount == u.price and u.price > 0)
                result[key] = Applied(offer_id=rule.id, offer_name=rule.name, discount=discount,
                                      free_units=free_units if rule.kind == BUY_GET_FREE else 0)
    return result
