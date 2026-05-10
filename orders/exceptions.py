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
"""


class OrderError(Exception):
    """Raised for invalid order state transitions or rule violations."""
    pass


class CartError(Exception):
    """Raised when the cart payload is invalid or empty."""
    pass


class InventoryError(Exception):
    """Raised when an inventory check or deduction fails."""
    pass


class MenuItemError(Exception):
    """Raised when a requested menu item is missing or unavailable."""
    pass


class ModifierError(Exception):
    """Raised when a modifier is not found or access is denied."""
    pass
