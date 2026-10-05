"""
How much one request may put into an order.

Well past any real order, low enough that a garbage or hostile request
can't write thousands of rows or a bill in the crores. Every entry point
(staff billing, QR guests, the aggregator webhook) checks the same numbers.
"""
from core.errors import UserError

MAX_LINE_QUANTITY = 999        # of one dish on one line
MAX_CART_LINES = 100           # lines in one request
MAX_NOTE_LENGTH = 200          # characters in a line's kitchen note
MAX_MODIFIERS_PER_LINE = 20

# A QR guest (no login) gets tighter limits: their items wait in review for
# staff, but nobody should have to clear a 999-portion line from a stranger.
GUEST_MAX_LINE_QUANTITY = 50
GUEST_MAX_CART_LINES = 30


class QuantityError(UserError, ValueError):
    """A quantity that isn't a whole number of portions in range; the message
    says what to fix."""


def line_quantity(value, maximum=MAX_LINE_QUANTITY):
    """A whole number of portions from 1 to `maximum`, or QuantityError with a message.

    JSON booleans are not numbers here (True would otherwise count as 1),
    and "2.5" or 2.5 is refused, not silently rounded.
    """
    if isinstance(value, bool):
        raise QuantityError("Quantity must be a whole number.")
    if isinstance(value, float):
        if not value.is_integer():
            raise QuantityError("Quantity must be a whole number.")
        value = int(value)
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        raise QuantityError("Quantity must be a whole number.")
    if quantity < 1:
        raise QuantityError("Quantity must be greater than zero.")
    if quantity > maximum:
        raise QuantityError(f"Quantity can be at most {maximum}.")
    return quantity
