"""
A second, independent copy of how a bill is totalled, as a pure function (no
database, and not importing the tax engine), written from the rules in
orders/services/tax_engine.py.

Why it exists: the bill maths is the heart of Rasova. The golden file pins
5,000 fixed bills; this copy lets the tests compare the real engine with an
independent reading of the rules on as many random bills as they like. Two
implementations agreeing is how a slip in either one gets caught.

Its totals are the totals of 27 September 2026, before the engine; they must
not change. Edit it only when a rule changes deliberately, in the same commit
as that change, and never to make it agree with new code by accident.

Deliberate changes so far (Phase 0, 27 Sep 2026):
  - the parcel charge carries GST at the rate stored on the bill, rounded on
    its own and added to the dishes' GST; bills from before, with no rate,
    keep an untaxed parcel charge
  - composition bills have no GST rows
  - the rows are allocated so they add up to the bill's GST, CGST and SGST,
    and carry the taxable value at each rate (0% included)
"""
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

TWO = Decimal("0.01")


def _q(amount):
    if amount is None:
        return Decimal("0.00")
    return Decimal(amount).quantize(TWO, rounding=ROUND_HALF_UP)


def _db2(value):
    """What a DecimalField(decimal_places=2) hands back after a save and load."""
    return Decimal(value).quantize(TWO)


def _split(gst_amount):
    cgst = (gst_amount / Decimal("2")).quantize(TWO, rounding=ROUND_HALF_UP)
    return cgst, gst_amount - cgst


def _share_out(exact, total):
    """Largest-remainder rounding: round each figure down to the paisa, then
    one more paisa to each of the biggest remainders until the figures make
    `total` (ties: the bigger figure, then the earlier one)."""
    down = [value.quantize(TWO, rounding=ROUND_FLOOR) for value in exact]
    paise = int((total - sum(down, Decimal("0"))) / TWO)
    assert 0 <= paise <= len(down), (exact, total)
    ranked = sorted(range(len(exact)), key=lambda n: (down[n] - exact[n], -exact[n], n))
    for n in ranked[:paise]:
        down[n] += TWO
    return down


def legacy_totals(spec, outlet_configs):
    """Totals for one money_scenarios spec, as the snapshot() dict shows them."""
    inclusive, composition = outlet_configs[spec["cfg"]]
    dishes = [
        {"total": _db2(it["total"]), "disc": _db2(it["disc"]), "gst": _db2(it["gst"])}
        for it in spec["items"]
        if it["status"] != "voided" and not it["comp"]
    ]
    dtype, dval = spec["dtype"], _db2(spec["dval"])
    parcel = _q(_db2(spec["parcel"]))
    parcel_rate = _db2(spec["parcel_rate"]) if spec.get("parcel_rate") else None

    # discounts, on the dishes only
    raw = sum((d["total"] for d in dishes), Decimal("0.0"))
    item_disc = Decimal("0.00")
    after_item = Decimal("0.00")
    for d in dishes:
        base = d["total"]
        if d["disc"] > 0:
            cut = base * (d["disc"] / Decimal("100"))
            item_disc += cut
            base -= cut
        after_item += base
    order_disc = Decimal("0.00")
    if dtype == "percentage" and (dval or 0) > 0:
        order_disc = after_item * (Decimal(dval) / Decimal("100"))
    elif dtype == "amount" and (dval or 0) > 0:
        order_disc = Decimal(str(dval))
    discount = _q(item_disc + order_disc)
    if discount > _q(raw):
        discount = _q(raw)
    factor = max(Decimal("0.0"), (after_item - order_disc) / after_item) if after_item > 0 else Decimal("1.0")

    # each dish's value after discounts, and the exact GST on or in it
    values, taxes = [], []
    for d in dishes:
        value = d["total"]
        if d["disc"] > 0:
            value = value * (1 - d["disc"] / Decimal("100"))
        value = value * factor
        values.append(value)
        rate = d["gst"]
        if composition:
            taxes.append(Decimal("0"))
        elif inclusive:
            taxes.append(value * rate / (Decimal("100") + rate) if rate > 0 else Decimal("0"))
        else:
            taxes.append((value * rate) / Decimal("100.0"))

    parcel_taxed = parcel > 0 and parcel_rate is not None and not composition
    parcel_exact = Decimal("0")
    if parcel_taxed and parcel_rate > 0:
        if inclusive:
            parcel_exact = parcel * parcel_rate / (Decimal("100") + parcel_rate)
        else:
            parcel_exact = parcel * parcel_rate / Decimal("100")

    dish_gst = _q(sum(taxes, Decimal("0.00")))          # the dishes, rounded once
    dish_tax = _share_out(taxes, dish_gst)
    parcel_tax = _q(parcel_exact)                        # the parcel, rounded on its own
    gst = dish_gst + parcel_tax

    if inclusive:
        after_discount = _q(raw - discount)
        sub = _q(after_discount - sum(dish_tax, Decimal("0")))
        grand = (after_discount + parcel).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ro = grand - after_discount - parcel
        dish_taxable = [g - t for g, t in zip(_share_out(values, after_discount), dish_tax)]
        parcel_taxable = parcel - parcel_tax
    else:
        sub = _q(raw)
        final = _q(sub - discount + gst)
        grand = (final + parcel).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ro = grand - final - parcel
        dish_taxable = _share_out(values, sub - discount)
        parcel_taxable = parcel
    cgst, sgst = _split(gst)

    # rows: taxable and tax per rate, CGST shared out so the rows add up
    rows = {}
    if not composition:
        for d, taxable, tax in zip(dishes, dish_taxable, dish_tax):
            row = rows.setdefault(d["gst"], [Decimal("0.00"), Decimal("0.00")])
            row[0] += taxable
            row[1] += tax
        if parcel_taxed:
            row = rows.setdefault(parcel_rate, [Decimal("0.00"), Decimal("0.00")])
            row[0] += parcel_taxable
            row[1] += parcel_tax
    rates = [rate for rate in sorted(rows) if rows[rate] != [0, 0]]
    row_cgst = _share_out([rows[rate][1] / 2 for rate in rates], cgst)
    bd = [
        ["gst", f"{rate:.2f}", f"{rows[rate][0]:.2f}", f"{rows[rate][1]:.2f}",
         f"{c:.2f}", f"{rows[rate][1] - c:.2f}"]
        for rate, c in zip(rates, row_cgst)
    ]

    out = {}
    if spec["parcel"] != "0":
        out["pgst"] = f"{parcel_tax:.2f}"
    out.update({
        "sub": f"{sub:.2f}", "gst": f"{gst:.2f}", "disc": f"{discount:.2f}",
        "grand": f"{grand:.2f}", "ro": f"{ro:.2f}",
        "cgst": f"{cgst:.2f}", "sgst": f"{sgst:.2f}", "bd": bd,
    })
    return out


def liquor_totals(spec, liquor_configs):
    """Totals for a bill with liquor lines (money_scenarios.liquor_snapshot's
    fields), for money_scenarios.LIQUOR_CONFIGS outlets. Added deliberately in
    Phase 1 (28 Sep 2026), written from the engine's rules and not from its
    code: liquor ("vat") lines are a second kind of tax, never GST; each kind
    is priced with or without its tax on its own (gst_inclusive,
    vat_inclusive); each kind's tax on the dishes is rounded once and shared
    back to them; the order discount is spread over food and drinks alike;
    the guest pays the dishes after discounts, the tax of every line priced
    without it, and the charges. legacy_totals() above is untouched: food
    bills keep their frozen maths."""
    gst_inclusive, vat_inclusive = liquor_configs[spec["cfg"]]
    lines = []
    for it in spec["items"]:
        if it["status"] == "voided" or it["comp"]:
            continue
        kind = it.get("kind", "gst")
        lines.append({
            "total": _db2(it["total"]), "disc": _db2(it["disc"]), "kind": kind,
            "rate": _db2(it["vat"] if kind == "vat" else it["gst"]),
            "inside": vat_inclusive if kind == "vat" else gst_inclusive,
        })
    dtype, dval = spec["dtype"], _db2(spec["dval"])
    parcel = _q(_db2(spec["parcel"]))
    parcel_rate = _db2(spec["parcel_rate"]) if spec.get("parcel_rate") else None

    # discounts, on the dishes only, spread over food and drinks by value
    raw = sum((line["total"] for line in lines), Decimal("0.0"))
    item_disc = Decimal("0.00")
    after_item = Decimal("0.00")
    for line in lines:
        base = line["total"]
        if line["disc"] > 0:
            cut = base * (line["disc"] / Decimal("100"))
            item_disc += cut
            base -= cut
        after_item += base
    order_disc = Decimal("0.00")
    if dtype == "percentage" and (dval or 0) > 0:
        order_disc = after_item * (Decimal(dval) / Decimal("100"))
    elif dtype == "amount" and (dval or 0) > 0:
        order_disc = Decimal(str(dval))
    discount = _q(item_disc + order_disc)
    if discount > _q(raw):
        discount = _q(raw)
    factor = max(Decimal("0.0"), (after_item - order_disc) / after_item) if after_item > 0 else Decimal("1.0")

    # each line's value after discounts, and the exact tax on or in it
    values, exact = [], []
    for line in lines:
        value = line["total"]
        if line["disc"] > 0:
            value = value * (1 - line["disc"] / Decimal("100"))
        value = value * factor
        values.append(value)
        rate = line["rate"]
        if line["inside"]:
            exact.append(value * rate / (Decimal("100") + rate) if rate > 0 else Decimal("0"))
        else:
            exact.append((value * rate) / Decimal("100.0"))

    # each kind's dish tax rounded once, then shared back to its lines
    tax = [Decimal("0.00")] * len(lines)
    kind_tax = {}
    for kind in ("gst", "vat"):
        where = [n for n, line in enumerate(lines) if line["kind"] == kind]
        kind_tax[kind] = _q(sum((exact[n] for n in where), Decimal("0.00")))
        for n, share in zip(where, _share_out([exact[n] for n in where], kind_tax[kind])):
            tax[n] = share

    # the parcel charge: GST at the bill's rate, rounded on its own
    parcel_taxed = parcel > 0 and parcel_rate is not None
    parcel_exact = Decimal("0")
    if parcel_taxed and parcel_rate > 0:
        if gst_inclusive:
            parcel_exact = parcel * parcel_rate / (Decimal("100") + parcel_rate)
        else:
            parcel_exact = parcel * parcel_rate / Decimal("100")
    parcel_tax = _q(parcel_exact)
    gst = kind_tax["gst"] + parcel_tax
    vat = kind_tax["vat"]
    cgst, sgst = _split(gst)

    # what the guest pays
    paid = _q(raw - discount)
    gross = _share_out(values, paid)
    taxable = [g - t if line["inside"] else g for line, g, t in zip(lines, gross, tax)]
    on_top = sum((t for line, t in zip(lines, tax) if not line["inside"]), Decimal("0.00"))
    if not gst_inclusive:
        on_top += parcel_tax
    final = _q(paid + on_top)
    grand = (final + parcel).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    ro = grand - final - parcel
    sub = _q(sum((tx if line["inside"] else line["total"] for line, tx in zip(lines, taxable)),
                 Decimal("0.0")))

    # rows by kind and rate, CGST shared out over the GST rows
    rows = {}
    for line, tx, t in zip(lines, taxable, tax):
        row = rows.setdefault((line["kind"], line["rate"]), [Decimal("0.00"), Decimal("0.00")])
        row[0] += tx
        row[1] += t
    if parcel_taxed:
        row = rows.setdefault(("gst", parcel_rate), [Decimal("0.00"), Decimal("0.00")])
        row[0] += parcel - parcel_tax if gst_inclusive else parcel
        row[1] += parcel_tax
    keys = [key for key in sorted(rows) if rows[key] != [0, 0]]
    gst_keys = [key for key in keys if key[0] == "gst"]
    row_cgst = dict(zip(gst_keys, _share_out([rows[key][1] / 2 for key in gst_keys], cgst)))
    bd = []
    for key in keys:
        kind, rate = key
        c = row_cgst.get(key, Decimal("0.00"))
        s = rows[key][1] - c if kind == "gst" else Decimal("0.00")
        bd.append([kind, f"{rate:.2f}", f"{rows[key][0]:.2f}", f"{rows[key][1]:.2f}", f"{c:.2f}", f"{s:.2f}"])

    # each kind of dish on its own: menu value, discount share, taxable, tax
    sec = []
    for kind in sorted({line["kind"] for line in lines}):
        where = [n for n, line in enumerate(lines) if line["kind"] == kind]
        menu = _q(sum((lines[n]["total"] for n in where), Decimal("0.0")))
        sec.append([
            kind, f"{menu:.2f}",
            f"{menu - sum((gross[n] for n in where), Decimal('0.00')):.2f}",
            f"{sum((taxable[n] for n in where), Decimal('0.00')):.2f}",
            f"{sum((tax[n] for n in where), Decimal('0.00')):.2f}",
        ])

    out = {}
    if spec["parcel"] != "0":
        out["pgst"] = f"{parcel_tax:.2f}"
    out.update({
        "sub": f"{sub:.2f}", "gst": f"{gst:.2f}", "disc": f"{discount:.2f}",
        "grand": f"{grand:.2f}", "ro": f"{ro:.2f}",
        "cgst": f"{cgst:.2f}", "sgst": f"{sgst:.2f}", "bd": bd,
        "vat": f"{vat:.2f}", "sec": sec,
    })
    return out
