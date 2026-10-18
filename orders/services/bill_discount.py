"""
The one place a bill's discount is set: a promo, a discount typed by hand,
or none.

A bill's discount is several fields that only make sense together: its type
and value, and, for a promo, which promo, its name, code and minimum order
(copies, so editing the promo later never changes the bill). They used to
be written separately by the bill screen, by sending an order and by two
model helpers, each remembering the promo rules on its own; one forgot, and
a bill could hold a promo with nothing off (18 Oct 2026). Now every path
calls these three functions, they always write all the fields together, and
the database refuses a promo with no discount (order_promo_has_discount).

Who may give how much, and the reason a typed discount needs, is
orders/services/discount_policy.py; the caller checks that. A promo needs
neither: the owner or a manager set it up in advance.

A promo's uses are the bills holding it (Promo.uses), so taking a promo off
a bill gives its use back without anything to undo. The callers run these
inside a transaction with the order row locked; then recalculate_totals().
"""
from decimal import Decimal

from orders.exceptions import OrderError

FIELDS = ["discount_type", "discount_value", "promo", "promo_name", "promo_code", "promo_min_order"]


class PromoRefused(OrderError):
    """The promo can't go on this bill now; the message says why."""


def set_promo(order, promo):
    """Put `promo` on the order with its values as they are now, or raise
    PromoRefused and leave the order as it was. Applying the promo the bill
    already holds takes its current values and no extra use."""
    locked = promo.locked()
    refusal = locked.refusal_for(order, order.menu_value())
    if refusal:
        raise PromoRefused(refusal)
    _write(order, locked.discount_type, locked.discount_value, promo=locked)


def set_manual(order, discount_type, value):
    """A discount typed by hand. It replaces any promo, and a value of 0 is
    no discount at all."""
    if discount_type not in ("percentage", "amount"):
        raise ValueError("invalid discount type")
    if Decimal(value) <= 0:
        return remove(order)
    _write(order, discount_type, value)


def remove(order):
    """No discount, and no promo."""
    _write(order, None, Decimal("0"))


def _write(order, discount_type, value, promo=None):
    order.discount_type = discount_type
    order.discount_value = Decimal(value).quantize(Decimal("0.01"))
    order.promo = promo
    order.promo_name = promo.name if promo else ""
    order.promo_code = promo.code if promo else ""
    order.promo_min_order = promo.min_order_value if promo else Decimal("0")
    order.save(update_fields=FIELDS)
