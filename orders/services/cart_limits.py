"""
How much one request may put into an order.

Well past any real order, low enough that a garbage or hostile request
can't write thousands of rows or a bill in the crores. Every entry point
(staff billing, QR guests, the aggregator webhook) checks the same numbers.
"""

MAX_LINE_QUANTITY = 999        # of one dish on one line
MAX_CART_LINES = 100           # lines in one request


def line_quantity(value, maximum=MAX_LINE_QUANTITY):
    """A whole number of portions from 1 to `maximum`, or ValueError with a message.

    JSON booleans are not numbers here (True would otherwise count as 1),
    and "2.5" or 2.5 is refused, not silently rounded.
    """
    if isinstance(value, bool):
        raise ValueError("Quantity must be a whole number.")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("Quantity must be a whole number.")
        value = int(value)
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        raise ValueError("Quantity must be a whole number.")
    if quantity < 1:
        raise ValueError("Quantity must be greater than zero.")
    if quantity > maximum:
        raise ValueError(f"Quantity can be at most {maximum}.")
    return quantity
