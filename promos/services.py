"""
A promo on a bill: taking a use when it is applied, and giving it back.

A promo's usage cap used to count taps, not bills: applying the same promo
twice used two of its uses, and removing or replacing the discount, or
cancelling the bill, never gave one back. Now the order remembers its promo
(Order.promo, Order.promo_name), applying the same promo again is free, and
every way a promo stops applying gives its use back. A paid bill keeps it.

All of these run inside the caller's transaction, with the order row
already locked (select_for_update), so two screens can't race on one bill.
"""


def attach_promo(order, promo):
    """Put `promo` on the order. Returns (ok, error). Does not save the order.

    The new promo is checked and its use taken first, so a promo that turns
    out to be invalid leaves whatever the order had before untouched."""
    if order.promo_id == promo.id:
        return True, ""
    ok, error = promo.validate_and_use(order.outlet, order.subtotal)
    if not ok:
        return False, error
    release_promo(order)
    order.promo = promo
    order.promo_name = promo.name
    return True, ""


def release_promo(order):
    """Take the order's promo off and give its use back. Does not save the
    order. A no-op when the order has none."""
    if order.promo_id is not None:
        order.promo.release_use()
    order.promo = None
    order.promo_name = ""
