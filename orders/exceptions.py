# orders/exceptions.py
"""
Custom exception hierarchy for the orders app.

Using typed exceptions lets callers catch exactly what they expect
without accidentally swallowing unrelated errors via bare `except Exception`.

Usage:
    from orders.exceptions import OrderError, CartError, InventoryError

    raise OrderError("Order is already closed")
    raise CartError("Cart is empty")
    raise InventoryError(f"Insufficient stock for {item.name}")

Each is a core.errors.UserError: its message is written for the person at
the screen, and views send it with core.errors.error_response.
"""
from core.errors import UserError


class OrderError(UserError):
    """Raised for invalid order state transitions or rule violations."""
    pass


class IssuedBillError(OrderError):
    """Something tried to re-total a bill that is paid or closed. An issued
    bill is a tax invoice and never changes; a correction is a refund. Every
    screen stops before this; Order.recalculate_totals() makes sure nothing
    gets past, including a screen holding an out-of-date copy of the bill.
    Being an OrderError, it reaches the user as a plain message wherever a
    screen already handles those."""

    def __init__(self, order_id=None, status="paid"):
        self.order_id = order_id
        self.status = status
        super().__init__(f"This bill is already {status}, so it can't be changed. Correct it with a refund.")


class CartError(UserError):
    """Raised when the cart payload is invalid or empty."""
    pass


class InventoryError(UserError):
    """Raised when an inventory check or deduction fails."""
    pass


class MenuItemError(UserError):
    """Raised when a requested menu item is missing or unavailable."""
    pass


class ModifierError(UserError):
    """Raised when a modifier is not found or access is denied."""
    pass
