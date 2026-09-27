"""
The tax engine: turns a bill's lines into every money figure the bill shows
and every tax figure the returns need. It is the only place Rasova does tax
maths. Order.recalculate_totals() runs it and stores what it returns (the
money fields, and the bill's tax record in Order.tax_summary); bills,
receipts, reports and returns read those. The carts' browser copy
(static/js/cart_tax.js) follows the same rules and is checked against this
module in CI.

Pure Python and Decimal: no database and no Django, so every rule can be
tested on its own.

The rules
---------
A bill is a list of lines: dishes, and charges such as the parcel charge.
Each line has an amount as the outlet prices it (tax extra, or tax
included), a tax kind ("gst" today; liquor VAT will add "vat"; None for a
line outside any tax) and a rate in percent.

1. Discounts. A dish's own discount comes off first. The order discount (a
   percentage of the dishes after their own discounts, or a flat amount) is
   then spread over the dishes in proportion to their value. Charges are
   never discounted. The discount never exceeds the dishes' value.
2. Tax on each line, exactly (nothing rounded yet): on top of the line's
   value when prices exclude tax (value x rate / 100), or inside it when
   they include tax (value x rate / (100 + rate)). An outlet on the
   composition scheme collects no GST.
3. The dishes' tax of each kind is the exact sum over them, rounded once to
   the paisa, half up. This is how Rasova has always totalled GST, so no
   bill's total changed when the engine arrived. Each charge's tax is
   rounded on its own, so adding a charge never moves the dishes' figures.
   The bill's tax of a kind is the dishes' plus the charges'.
4. The per-rate rows (the bill's tax breakdown, and every return) add up to
   exactly those totals. The dishes' rounded tax is allocated back to them:
   each dish's exact figure is rounded down to the paisa, and the paise
   still owed go to the largest remainders (ties: the larger figure first,
   then line order). Taxable values are allocated the same way, to the
   bill's taxable total. CGST is half the GST rounded half up and SGST the
   rest; each row's CGST is allocated the same way, so the rows add up to
   both.
5. The grand total is rounded to the rupee, half up; the difference is the
   round-off.

The expressions in _dish_figures() and compute() deliberately match the
maths of the bills totalled before the engine, digit for digit: the golden
bills in orders/tests/golden/ hold it to that.
"""
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

GST = "gst"

PAISA = Decimal("0.01")
RUPEE = Decimal("1")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")

SUMMARY_VERSION = 1


def to_paisa(amount):
    """Round to the paisa, half up."""
    return Decimal(amount).quantize(PAISA, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Line:
    """One line of a bill, as the outlet prices it."""
    amount: Decimal                          # quantity x (price + modifiers)
    rate: Decimal = ZERO                     # percent
    kind: str | None = GST                   # None: outside any tax
    item_discount_pct: Decimal = ZERO
    charge: str | None = None                # e.g. "parcel": a charge, never discounted


@dataclass(frozen=True)
class RateRow:
    """Tax of one kind at one rate, across a bill (or many bills)."""
    kind: str
    rate: Decimal
    taxable: Decimal
    tax: Decimal
    cgst: Decimal = ZERO                     # GST only
    sgst: Decimal = ZERO                     # GST only


@dataclass(frozen=True)
class ChargeTax:
    """A charge (the parcel charge) and the tax on or inside it."""
    name: str
    amount: Decimal
    kind: str | None
    rate: Decimal
    taxable: Decimal
    tax: Decimal


@dataclass(frozen=True)
class Bill:
    """Everything the engine works out for one bill."""
    subtotal: Decimal        # dishes: menu value (tax extra) or value before GST (tax included)
    discount: Decimal
    charges: Decimal         # charges as billed
    gst: Decimal
    cgst: Decimal
    sgst: Decimal
    grand_total: Decimal
    round_off: Decimal
    rows: tuple              # RateRow, sorted by kind and rate
    charge_taxes: tuple      # ChargeTax, in line order

    @property
    def charge_tax(self):
        return sum((c.tax for c in self.charge_taxes), ZERO)

    def summary(self):
        """The bill's tax record, as stored in Order.tax_summary (JSON)."""
        return {
            "v": SUMMARY_VERSION,
            "rows": [
                {"kind": r.kind, "rate": _s(r.rate), "taxable": _s(r.taxable), "tax": _s(r.tax),
                 "cgst": _s(r.cgst), "sgst": _s(r.sgst)}
                for r in self.rows
            ],
            "charges": [
                {"name": c.name, "amount": _s(c.amount), "kind": c.kind, "rate": _s(c.rate),
                 "taxable": _s(c.taxable), "tax": _s(c.tax)}
                for c in self.charge_taxes
            ],
        }


def rows_from_summary(summary):
    """RateRow objects back from a stored tax record."""
    return [
        RateRow(kind=row["kind"], rate=Decimal(row["rate"]), taxable=Decimal(row["taxable"]),
                tax=Decimal(row["tax"]), cgst=Decimal(row["cgst"]), sgst=Decimal(row["sgst"]))
        for row in (summary or {}).get("rows", [])
    ]


def split_gst(gst):
    """CGST is half the GST rounded half up; SGST is the rest, so the two
    always add up to the GST exactly."""
    cgst = (gst / Decimal("2")).quantize(PAISA, rounding=ROUND_HALF_UP)
    return cgst, gst - cgst


def allocate(exact, total):
    """Round each exact figure to the paisa so that together they make
    `total` exactly (rule 4): round every figure down, then give the paise
    still owed to the largest remainders; ties go to the larger figure, then
    the earlier one. `total` must lie between the rounded-down sum and that
    sum plus one paisa per figure."""
    floors = [Decimal(value).quantize(PAISA, rounding=ROUND_FLOOR) for value in exact]
    owed = int((Decimal(total) - sum(floors, ZERO)) / PAISA)
    if not 0 <= owed <= len(floors):
        raise ValueError(f"cannot allocate {total} over {list(exact)}")
    order = sorted(range(len(floors)), key=lambda i: (-(exact[i] - floors[i]), -exact[i], i))
    for i in order[:owed]:
        floors[i] += PAISA
    return floors


def _s(amount):
    return f"{Decimal(amount):.2f}"


def _taxed(line, composition):
    """Whether tax applies to this line at all."""
    if line.kind is None:
        return False
    if line.kind == GST and composition:
        return False
    return True


def _dish_figures(dishes, factor, prices_include_tax, composition):
    """For each dish: its exact value after discounts, and the exact tax on or
    inside it (rule 2)."""
    values, taxes = [], []
    for line in dishes:
        value = line.amount
        if line.item_discount_pct > 0:
            value = value * (1 - line.item_discount_pct / HUNDRED)
        value = value * factor
        values.append(value)
        if not _taxed(line, composition):
            taxes.append(Decimal("0"))
        elif prices_include_tax:
            taxes.append(value * line.rate / (HUNDRED + line.rate) if line.rate > 0 else Decimal("0"))
        else:
            taxes.append((value * line.rate) / Decimal("100.0"))
    return values, taxes


def compute(lines, *, prices_include_tax=False, composition=False,
            discount_type=None, discount_value=ZERO):
    """Total one bill. See the module docstring for the rules."""
    lines = list(lines)
    dishes = [line for line in lines if line.charge is None]
    charges = [line for line in lines if line.charge is not None]

    # Rule 1: discounts, on the dishes only
    raw = sum((line.amount for line in dishes), Decimal("0.0"))
    item_discount = Decimal("0.00")
    after_item_discounts = Decimal("0.00")
    for line in dishes:
        value = line.amount
        if line.item_discount_pct > 0:
            cut = value * (line.item_discount_pct / HUNDRED)
            item_discount += cut
            value -= cut
        after_item_discounts += value

    order_discount = Decimal("0.00")
    if discount_type == "percentage" and (discount_value or 0) > 0:
        order_discount = after_item_discounts * (Decimal(discount_value) / HUNDRED)
    elif discount_type == "amount" and (discount_value or 0) > 0:
        order_discount = Decimal(str(discount_value))

    subtotal_menu = to_paisa(raw)
    discount = to_paisa(item_discount + order_discount)
    if discount > subtotal_menu:
        discount = subtotal_menu

    if after_item_discounts > 0:
        factor = max(Decimal("0.0"), (after_item_discounts - order_discount) / after_item_discounts)
    else:
        factor = Decimal("1.0")

    # Rule 2: exact tax on every line
    dish_values, dish_taxes = _dish_figures(dishes, factor, prices_include_tax, composition)
    charge_amounts = [to_paisa(line.amount) for line in charges]
    charge_taxes = []
    for line, amount in zip(charges, charge_amounts):
        if not _taxed(line, composition) or line.rate <= 0:
            charge_taxes.append(Decimal("0"))
        elif prices_include_tax:
            charge_taxes.append(amount * line.rate / (HUNDRED + line.rate))
        else:
            charge_taxes.append(amount * line.rate / HUNDRED)

    # Rules 3 and 4: the dishes' tax of each kind rounded once and allocated
    # back to them; each charge's tax rounded on its own.
    dish_tax = [ZERO] * len(dishes)
    for kind in sorted({line.kind for line in dishes if line.kind is not None}):
        positions = [i for i, line in enumerate(dishes) if line.kind == kind]
        exact = Decimal("0.00")
        for i in positions:
            exact += dish_taxes[i]
        for i, share in zip(positions, allocate([dish_taxes[i] for i in positions], to_paisa(exact))):
            dish_tax[i] = share
    charge_tax = [to_paisa(tax) for tax in charge_taxes]

    all_lines = dishes + charges
    line_tax = dish_tax + charge_tax
    gst = sum((tax for line, tax in zip(all_lines, line_tax) if line.kind == GST), ZERO)
    cgst, sgst = split_gst(gst)
    charges_total = sum(charge_amounts, ZERO)

    # Totals (rule 5), and each line's taxable value
    if prices_include_tax:
        after_discount = to_paisa(raw - discount)
        subtotal = to_paisa(after_discount - sum(dish_tax, ZERO))   # the dishes' value before tax
        grand_total = (after_discount + charges_total).quantize(RUPEE, rounding=ROUND_HALF_UP)
        round_off = grand_total - after_discount - charges_total
        gross = allocate(dish_values, after_discount)
        dish_taxable = [value - tax for value, tax in zip(gross, dish_tax)]
        charge_taxable = [amount - tax for amount, tax in zip(charge_amounts, charge_tax)]
    else:
        subtotal = subtotal_menu
        taxable_total = subtotal - discount
        final_total = to_paisa(taxable_total + sum(line_tax, ZERO))   # every kind of tax on top
        grand_total = (final_total + charges_total).quantize(RUPEE, rounding=ROUND_HALF_UP)
        round_off = grand_total - final_total - charges_total
        dish_taxable = allocate(dish_values, taxable_total)
        charge_taxable = list(charge_amounts)

    rows = _rate_rows(all_lines, dish_taxable + charge_taxable, line_tax, composition, cgst)
    return Bill(
        subtotal=subtotal, discount=discount, charges=charges_total,
        gst=gst, cgst=cgst, sgst=sgst, grand_total=grand_total, round_off=round_off,
        rows=tuple(rows),
        charge_taxes=tuple(
            ChargeTax(name=line.charge, amount=amount, kind=line.kind if _taxed(line, composition) else None,
                      rate=line.rate, taxable=taxable, tax=tax)
            for line, amount, taxable, tax in zip(charges, charge_amounts, charge_taxable, charge_tax)
        ),
    )


def _rate_rows(lines, taxable, tax, composition, cgst_total):
    """Group the lines' allocated figures by kind and rate (rule 4). Lines
    outside tax, and all GST on the composition scheme, make no rows."""
    grouped = {}
    for line, line_taxable, line_tax in zip(lines, taxable, tax):
        if not _taxed(line, composition):
            continue
        key = (line.kind, Decimal(line.rate).quantize(PAISA))
        row = grouped.setdefault(key, [ZERO, ZERO])
        row[0] += line_taxable
        row[1] += line_tax
    keys = [key for key in sorted(grouped) if grouped[key] != [ZERO, ZERO]]

    gst_keys = [key for key in keys if key[0] == GST]
    row_cgst = dict(zip(gst_keys, allocate([grouped[key][1] / 2 for key in gst_keys], cgst_total)))
    return [
        RateRow(kind=kind, rate=rate, taxable=grouped[(kind, rate)][0], tax=grouped[(kind, rate)][1],
                cgst=row_cgst.get((kind, rate), ZERO),
                sgst=grouped[(kind, rate)][1] - row_cgst[(kind, rate)] if kind == GST else ZERO)
        for kind, rate in keys
    ]
