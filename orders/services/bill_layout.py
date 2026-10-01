"""
What a bill says about money, line by line: one layout for every bill
Rasova shows a guest (the printed bill and split-bill slip, the thermal and
A4 web bills and their PDF, and the WhatsApp bill link).

Every figure comes from the bill's tax record (Order.tax_summary, written by
orders/services/tax_engine.py), never from today's outlet settings, so an
issued bill never rewords itself. The renderers only draw what this returns.

The rows add up. Read top to bottom, a guest or an inspector gets the total:

    Subtotal        the dishes at menu prices (a free dish shows at 0)
    Discount        minus every discount, a dish's own and the bill's
    CGST / SGST     by rate, when the price excludes GST (tax added on top)
    VAT             by rate, when the price excludes VAT
    Parcel charge   as billed; its GST is in the rows above
    Round off
    = TOTAL

When the prices include a tax, that tax is already inside the subtotal, so
it is not a row: it is shown under the total as "included", with the
taxable value, so the bill still states the rate and amount of tax a tax
invoice must (CGST Rules, rule 46). Printing it as a row as well, after a
subtotal that was already the taxable value, is what made the old bills
not add up (927 + 108 - 115 is not 1035).

A composition outlet's bill is a bill of supply and says it can't collect
tax (rule 49); an outlet without a GSTIN issues a plain bill.
"""
from dataclasses import dataclass
from decimal import Decimal

from orders.services.tax_engine import COMPOSITION, GST, REGULAR, UNREGISTERED, VAT, ZERO

TAX_INVOICE = "Tax Invoice"
BILL_OF_SUPPLY = "Bill of Supply"
PLAIN_BILL = "Bill"
COMPOSITION_STATEMENT = "Composition taxable person, not eligible to collect tax on supplies"

_KIND_LABEL = {GST: "GST", VAT: "VAT"}


@dataclass(frozen=True)
class DishLine:
    quantity: int
    name: str
    is_veg: bool
    amount: Decimal          # quantity x (price + modifiers); 0 for a free dish
    free: bool
    modifiers: tuple = ()    # names
    note: str = ""           # the kitchen note, for bills that show it


@dataclass(frozen=True)
class MoneyRow:
    key: str                 # subtotal, discount, cgst, sgst, vat, charge, round_off
    label: str
    amount: Decimal          # signed: the rows add up to the total

    @property
    def negative(self):
        return self.amount < 0

    @property
    def magnitude(self):
        """The amount without its sign, for bills that print the sign apart."""
        return abs(self.amount)


@dataclass(frozen=True)
class IncludedTax:
    """A tax inside the prices, at one rate: shown, never added again."""
    kind: str
    rate: Decimal
    taxable: Decimal
    cgst: Decimal
    sgst: Decimal
    tax: Decimal

    @property
    def label(self):
        return f"{_KIND_LABEL.get(self.kind, self.kind.upper())} {rate_text(self.rate)}"

    @property
    def half_rate(self):
        """The CGST (and SGST) rate: half the GST rate, as "2.5%"."""
        return rate_text(self.rate / 2)


@dataclass(frozen=True)
class BillLayout:
    title: str
    scheme: str
    statement: str           # the composition statement, or ""
    lines: tuple             # DishLine
    rows: tuple              # MoneyRow, adding up to total
    total: Decimal
    included: tuple          # IncludedTax
    prices_include_gst: bool
    state_tax: str = "SGST"  # "UTGST" in a union territory without a legislature

    @property
    def included_total(self):
        return sum((tax.tax for tax in self.included), ZERO)

    @property
    def included_taxable(self):
        return sum((tax.taxable for tax in self.included), ZERO)


def rate_text(rate):
    """2.50 -> "2.5%", 9.00 -> "9%", 0 -> "0%"."""
    text = f"{Decimal(rate):.2f}".rstrip("0").rstrip(".")
    return f"{text}%"


def money(amount):
    """Two places, no currency sign: 1035 -> "1035.00", -115 -> "-115.00"."""
    return f"{Decimal(amount):.2f}"


def _title(scheme):
    if scheme == COMPOSITION:
        return BILL_OF_SUPPLY
    if scheme == UNREGISTERED:
        return PLAIN_BILL
    return TAX_INVOICE


def _charges(order, live_items):
    """The bill's charges (the parcel charge) as billed: name, amount, kind,
    rate, taxable, tax. From the tax record; bills from before the record
    are worked out the way they were billed, as Order.tax_rows() does."""
    summary = order.tax_summary
    if summary is not None and "charges" in summary:
        return [
            (c["name"], Decimal(c["amount"]), c["kind"], Decimal(c["rate"]),
             Decimal(c["taxable"]), Decimal(c["tax"]))
            for c in summary["charges"]
        ]
    bill = order._run_tax_engine(live_items, as_billed_before_record=True)
    return [(c.name, c.amount, c.kind, c.rate, c.taxable, c.tax) for c in bill.charge_taxes]


def _inside(kind, sections, charges):
    """Whether this kind of tax is inside the prices, read from the record:
    a section's value after discount is its taxable value when tax was added
    on top, and its taxable value plus its tax when the tax was inside. An
    outlet sets this per kind, so every line of a kind agrees. A kind with
    no tax at all is never shown either way."""
    for section in sections:
        if section.kind == kind and section.tax > 0:
            return section.menu - section.discount == section.taxable + section.tax
    for name, amount, charge_kind, rate, taxable, tax in charges:
        if charge_kind == kind and tax > 0:
            return amount == taxable + tax
    return False


_CHARGE_LABEL = {"parcel": "Parcel charge"}


def bill_layout(order):
    """The BillLayout of an order. Reads order.items, so prefetch them
    (with menu_item and modifiers) when laying out many bills."""
    items = [i for i in order.items.all() if i.status != "voided"]
    items.sort(key=lambda i: i.id)
    live = [i for i in items if not i.is_complimentary]

    lines = tuple(
        DishLine(
            quantity=item.quantity,
            name=str(item.menu_item.name) if item.menu_item else "Item",
            is_veg=bool(getattr(item.menu_item, "is_veg", False)),
            amount=ZERO if item.is_complimentary else Decimal(item.total_price),
            free=bool(item.is_complimentary),
            modifiers=tuple(m.name for m in item.modifiers.all()),
            note=item.notes or "",
        )
        for item in items
    )

    sections = order.tax_sections()
    rows = order.tax_rows()
    charges = _charges(order, live)
    scheme = order.gst_scheme
    # A union territory without a legislature charges UTGST in place of SGST.
    state_tax = "UTGST" if getattr(order.outlet, "uses_utgst", False) else "SGST"

    money_rows = [MoneyRow("subtotal", "Subtotal", sum((s.menu for s in sections), ZERO))]
    discount = sum((s.discount for s in sections), ZERO)
    if discount > 0:
        money_rows.append(MoneyRow("discount", "Discount", -discount))

    included = []
    for kind in (GST, VAT):
        kind_rows = [row for row in rows if row.kind == kind and row.tax > 0]
        if not kind_rows:
            continue
        if _inside(kind, sections, charges):
            included.extend(
                IncludedTax(kind=kind, rate=row.rate, taxable=row.taxable,
                            cgst=row.cgst, sgst=row.sgst, tax=row.tax)
                for row in kind_rows
            )
            continue
        for row in kind_rows:
            if kind == GST:
                half = rate_text(row.rate / 2)
                money_rows.append(MoneyRow("cgst", f"CGST {half}", row.cgst))
                money_rows.append(MoneyRow("sgst", f"{state_tax} {half}", row.sgst))
            else:
                money_rows.append(MoneyRow("vat", f"VAT {rate_text(row.rate)}", row.tax))

    for name, amount, *_ in charges:
        if amount:
            money_rows.append(MoneyRow("charge", _CHARGE_LABEL.get(name, name.title()), amount))

    if order.round_off:
        money_rows.append(MoneyRow("round_off", "Round off", Decimal(order.round_off)))

    return BillLayout(
        title=_title(scheme),
        scheme=scheme,
        statement=COMPOSITION_STATEMENT if scheme == COMPOSITION else "",
        lines=lines,
        rows=tuple(money_rows),
        total=Decimal(order.grand_total),
        included=tuple(included),
        prices_include_gst=scheme == REGULAR and any(t.kind == GST for t in included),
        state_tax=state_tax,
    )
