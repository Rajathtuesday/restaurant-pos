"""
Money, written one way everywhere (V12, 13 Nov 2026).

The same cart used to show "₹430", "₹20.00" and "₹450" side by side. The
rule now: whole rupees when there are no paise ("₹430"), two decimals when
there are ("₹20.50"), Indian grouping ("₹1,25,000"), a minus before the
sign ("-₹50"). static/js/core/money.js is the same rule for the screens;
both are tested against one table of examples (core/tests/test_money.py and
static/js/tests/money.test.mjs).
"""
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def _indian(whole):
    """'1234567' -> '12,34,567': the last three digits, then pairs."""
    if len(whole) <= 3:
        return whole
    head, tail = whole[:-3], whole[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    if head:
        pairs.insert(0, head)
    return ",".join(pairs + [tail])


def rupees(value):
    """₹ text for an amount (Decimal, int, float or a numeric string)."""
    try:
        amount = Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return "₹0"
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    whole, paise = f"{amount:.2f}".split(".")
    text = "₹" + _indian(whole) + ("" if paise == "00" else "." + paise)
    return sign + text
