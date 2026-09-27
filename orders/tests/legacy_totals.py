"""
A frozen copy of how Order.recalculate_totals() totals a bill, as a pure
function (no database), taken from orders/models.py on 27 September 2026.

Why it exists: the liquor VAT work rewrites the bill maths. Every bill that
has no liquor must still come out exactly as it does today. The golden file
pins 5,000 fixed bills; this copy lets the tests compare old and new maths on
as many random bills as they like.

Edit it only when a change to the maths is deliberate (Phase 0's GST on the
parcel charge is the one planned case), and in the same commit as that change.
It must never be "fixed" to match new code by accident.
"""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

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


def _breakdown(items, factor, inclusive):
    by_rate = defaultdict(Decimal)
    for it in items:
        rate = it["gst"]
        base = it["total"]
        if it["disc"] > 0:
            base = base * (1 - it["disc"] / Decimal("100"))
        taxable = base * factor
        if inclusive:
            amount = (taxable * rate / (Decimal("100") + rate)).quantize(TWO) if rate > 0 else Decimal("0.00")
        else:
            amount = (taxable * rate / Decimal("100")).quantize(TWO)
        by_rate[rate] += amount
    rows = []
    for rate, amount in sorted(by_rate.items()):
        if amount <= 0:
            continue
        half = (rate / 2).quantize(TWO, rounding=ROUND_HALF_UP)
        cgst, sgst = _split(amount)
        rows.append([str(rate), str(half), str(half), str(cgst), str(sgst)])
    return rows


def _exclusive(items, dtype, dval, parcel, composition):
    subtotal = _q(sum((it["total"] for it in items), Decimal("0.0")))
    item_disc = Decimal("0.00")
    after_item = Decimal("0.00")
    for it in items:
        base = it["total"]
        if it["disc"] > 0:
            cut = base * (it["disc"] / Decimal("100"))
            item_disc += cut
            base -= cut
        after_item += base
    order_disc = Decimal("0.00")
    if dtype == "percentage" and (dval or 0) > 0:
        order_disc = after_item * (Decimal(dval) / Decimal("100"))
    elif dtype == "amount" and (dval or 0) > 0:
        order_disc = Decimal(str(dval))
    discount = _q(item_disc + order_disc)
    if discount > subtotal:
        discount = subtotal
    taxable_amount = subtotal - discount
    factor = max(Decimal("0.0"), (after_item - order_disc) / after_item) if after_item > 0 else Decimal("1.0")
    gst = Decimal("0.00")
    if not composition:
        for it in items:
            base = it["total"]
            if it["disc"] > 0:
                base = base * (1 - it["disc"] / Decimal("100"))
            gst += (base * factor * it["gst"]) / Decimal("100.0")
    gst = _q(gst)
    final = _q(taxable_amount + gst)
    parcel = _q(parcel or Decimal("0"))
    grand = (final + parcel).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return {
        "sub": subtotal, "gst": gst, "disc": discount, "grand": grand,
        "ro": grand - final - parcel, "bd": _breakdown(items, factor, False),
    }


def _inclusive(items, dtype, dval, parcel, composition):
    raw = sum((it["total"] for it in items), Decimal("0.0"))
    item_disc = Decimal("0.00")
    after_item = Decimal("0.00")
    for it in items:
        inc = it["total"]
        if it["disc"] > 0:
            cut = inc * (it["disc"] / Decimal("100"))
            item_disc += cut
            inc -= cut
        after_item += inc
    order_disc = Decimal("0.00")
    if dtype == "percentage" and (dval or 0) > 0:
        order_disc = after_item * (Decimal(dval) / Decimal("100"))
    elif dtype == "amount" and (dval or 0) > 0:
        order_disc = Decimal(str(dval))
    discount = _q(item_disc + order_disc)
    if discount > raw:
        discount = raw
    after_discount = _q(raw - discount)
    factor = max(Decimal("0.0"), (after_item - order_disc) / after_item) if after_item > 0 else Decimal("1.0")
    gst = Decimal("0.00")
    if not composition:
        for it in items:
            inc = it["total"]
            if it["disc"] > 0:
                inc = inc * (1 - it["disc"] / Decimal("100"))
            inc = inc * factor
            rate = it["gst"]
            if rate > 0:
                gst += inc * rate / (Decimal("100") + rate)
    gst = _q(gst)
    subtotal = _q(after_discount - gst)
    parcel = _q(parcel or Decimal("0"))
    grand = (after_discount + parcel).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return {
        "sub": subtotal, "gst": gst, "disc": discount, "grand": grand,
        "ro": grand - after_discount - parcel, "bd": _breakdown(items, factor, True),
    }


def legacy_totals(spec, outlet_configs):
    """Totals for one money_scenarios spec, as the snapshot() dict would show them."""
    inclusive, composition = outlet_configs[spec["cfg"]]
    items = [
        {"total": _db2(it["total"]), "disc": _db2(it["disc"]), "gst": _db2(it["gst"])}
        for it in spec["items"]
        if it["status"] != "voided" and not it["comp"]
    ]
    dval = _db2(spec["dval"])
    parcel = _db2(spec["parcel"])
    calc = _inclusive if inclusive else _exclusive
    out = calc(items, spec["dtype"], dval, parcel, composition)
    cgst, sgst = _split(out["gst"])
    return {
        "sub": f"{out['sub']:.2f}", "gst": f"{out['gst']:.2f}", "disc": f"{out['disc']:.2f}",
        "grand": f"{out['grand']:.2f}", "ro": f"{out['ro']:.2f}",
        "cgst": f"{cgst:.2f}", "sgst": f"{sgst:.2f}", "bd": out["bd"],
    }
