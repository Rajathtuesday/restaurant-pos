"""
Tax by kind and rate across many bills, for the GSTR-1 export and the tax
inspection screen.

Every figure comes from each bill's own tax record (Order.tax_rows()), so a
return always adds up to exactly the bills it covers, and nothing here
re-does the tax maths.
"""
from decimal import Decimal


def rate_totals(orders):
    """Sum the bills' tax rows. Returns {(kind, rate): {"taxable", "tax",
    "cgst", "sgst"}} with Decimal values; rate is a Decimal like 5.00.

    Composition-scheme bills are bills of supply and carry no GST rows. Bills
    totalled before the tax record existed (27 Sep 2026) are worked out by
    the same engine from their lines (see Order.tax_rows), so only those
    fetch their lines.
    """
    recorded = orders.filter(tax_summary__isnull=False)
    older = orders.filter(tax_summary__isnull=True).select_related("outlet").prefetch_related("items")

    totals = {}
    for group in (recorded, older):
        for order in group:
            for row in order.tax_rows():
                entry = totals.setdefault((row.kind, row.rate), {
                    "taxable": Decimal("0.00"), "tax": Decimal("0.00"),
                    "cgst": Decimal("0.00"), "sgst": Decimal("0.00"),
                })
                entry["taxable"] += row.taxable
                entry["tax"] += row.tax
                entry["cgst"] += row.cgst
                entry["sgst"] += row.sgst
    return totals


def operator_supplies(orders):
    """The value of orders taken through an e-commerce operator, whose GST
    the app pays (CGST Act, section 9(5)), by outlet and app:
    {(outlet_id, source): Decimal}. The value is each bill's taxable value
    (its dishes after discount and its charges), from its tax record; such a
    bill carries no GST of its own, so that is everything the restaurant
    supplied. For GSTR-1 Table 14."""
    from orders.services.tax_engine import OPERATOR

    totals = {}
    for order in orders.filter(tax_summary__scheme=OPERATOR):
        summary = order.tax_summary
        value = sum((Decimal(s["taxable"]) for s in summary.get("sections", [])), Decimal("0.00"))
        value += sum((Decimal(c["taxable"]) for c in summary.get("charges", [])), Decimal("0.00"))
        key = (order.outlet_id, order.source)
        totals[key] = totals.get(key, Decimal("0.00")) + value
    return totals
