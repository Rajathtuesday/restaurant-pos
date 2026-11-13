"""
The parcel charge: the one rule, used by the bill and copied by the screens.

The bill gets it from toggle_parcel (orders/views/order_actions.py). Before
an order exists, a billing screen shows what the bill will charge with its
own copy of this rule (static/js/billing/parcel.js), and
orders/tests/test_parcel_parity.py holds the two equal on random carts and
on a real billing page: a screen showing one number and the bill another
is how the parcel bug of October happened (the cart said 252, the bill 264).

The rule:
  a dish with its own parcel charge: that, times its quantity, for every
  such dish; if no dish in the order has one, the outlet's rate times every
  item when the outlet charges per item, else the outlet's rate once.
"""
from decimal import Decimal

from core.money import rupees

ZERO = Decimal("0")


def parcel_charge(lines, rate, per_item):
    """The charge for an order's live items.

    ``lines`` is (the dish's own parcel charge or None, quantity) for each
    item that is not voided; ``rate`` the outlet's charge; ``per_item``
    whether the outlet charges it on every item or once.
    """
    lines = [(Decimal(str(own or 0)), int(quantity)) for own, quantity in lines]
    own_total = sum((own * quantity for own, quantity in lines if own > 0), ZERO)
    if own_total > 0:
        return own_total
    rate = Decimal(str(rate or 0))
    if per_item:
        return rate * sum(quantity for _own, quantity in lines)
    return rate


def offer_text(outlet):
    """The parcel button's words: "Add Parcel ₹2.50/item" or "Add Parcel ₹20 flat"."""
    each = "/item" if outlet.parcel_charge_per_item else " flat"
    return f"Add Parcel {rupees(outlet.parcel_charge_amount or 0)}{each}"
