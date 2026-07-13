# orders/services/payment_service.py
# ============================================================
# Changes vs original:
#
# 1. Overpayment allowed (QSR cash reality):
#    BEFORE: raises ValidationError if amount > remaining
#    AFTER:  records only `remaining` as amount, returns change_due
#    WHY recording `remaining` not the tendered amount:
#      Recording ₹500 for a ₹480 bill makes the Z-report show ₹500
#      cash collected — the restaurant never kept that ₹20.
#
# 2. paid → closed in one place:
#    BEFORE: service set "paid", view set "closed" — callers that
#            skipped the view left orders stuck at "paid".
#    AFTER:  service transitions all the way to "closed" and sets
#            closed_at. Returns order_closed=True for callers.
#
# 3. Row-locked order (already existed, kept as-is).
# ============================================================

import logging
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from orders.models import Payment
from inventory.unit_conversion import convert_quantity, IncompatibleUnitsError

logger = logging.getLogger("pos.orders")


@transaction.atomic
def process_payment(order, method, amount, user=None, reference=None):
    """
    Records a payment against an order.

    Parameters
    ----------
    order     : Order instance (will be re-fetched under lock)
    method    : "cash" | "upi" | "card"
    amount    : Decimal — the amount TENDERED (may exceed remaining for cash)
    user      : User instance or None
    reference : str or None — external transaction ID (e.g. Razorpay payment id)

    Returns
    -------
    {
        "payment":      Payment instance,
        "remaining":    Decimal — balance still due after this payment (≥0),
        "change_due":   Decimal — change to return to the customer (≥0),
        "order_closed": bool   — True if this payment fully settled the order,
    }

    Raises
    ------
    ValidationError  — zero/negative amount, or order already fully paid.
    """
    # Lock the order row to prevent concurrent over-payment.
    order = type(order).objects.select_for_update().get(id=order.id)

    amount = Decimal(str(amount))

    if amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")

    paid_total = (
        order.payments
        .exclude(method="refund")
        .aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )
    remaining = order.grand_total - paid_total

    if remaining <= 0:
        raise ValidationError("This order is already fully paid.")

    # ---------------------------------------------------------------
    # Overpayment: common when a customer hands over a ₹500 note.
    # We record only what was kept; change_due tells the cashier
    # how much to give back.
    # ---------------------------------------------------------------
    if amount > remaining:
        change_due       = amount - remaining
        amount_to_record = remaining
    else:
        change_due       = Decimal("0.00")
        amount_to_record = amount

    payment = Payment.objects.create(
        order=order,
        method=method,
        amount=amount_to_record,
        created_by=user,
        reference=reference,
    )

    logger.info(
        "Payment | order=%s | method=%s | tendered=%.2f | "
        "recorded=%.2f | change=%.2f | user=%s",
        order.id, method, float(amount), float(amount_to_record),
        float(change_due), getattr(user, "username", "system"),
    )

    # ---------------------------------------------------------------
    # Close the order if it is now fully settled.
    # This single location ensures split_pay, token billing, and any
    # future callers all produce a consistent "closed" state.
    # ---------------------------------------------------------------
    new_paid_total = paid_total + amount_to_record
    order_closed   = False

    if new_paid_total >= order.grand_total:
        order.status    = "closed"
        order.closed_at = timezone.now()
        order.save(update_fields=["status", "closed_at"])
        order_closed = True
        logger.info("Order #%s fully paid and closed.", order.id)

        # ---------------------------------------------------------------
        # QSR Inventory Deduction (counter orders only).
        #
        # Fine dining deducts at the KOT/serve stage (kitchen flow).
        # QSR has no KOT serve step — the cashier takes the order and
        # hands it over immediately, so we deduct at payment close.
        #
        # Failures are LOGGED but never block payment — the transaction
        # is already recorded and the customer's change is due.
        # ---------------------------------------------------------------
        if order.source == "counter":
            _deduct_inventory_for_order(order)

    return {
        "payment":      payment,
        "remaining":    max(Decimal("0.00"), order.grand_total - new_paid_total),
        "change_due":   change_due,
        "order_closed": order_closed,
    }


def _deduct_inventory_for_order(order):
    """
    Deducts inventory for every non-voided item in a QSR counter order.
    Called once, after the order transitions to 'closed'.

    Deliberately outside the atomic block above so that an inventory
    deduction failure doesn't roll back the payment record.
    """
    from django.core.exceptions import ValidationError as DjangoValidationError

    items = order.items.exclude(status="voided").select_related("menu_item")

    for order_item in items:
        if not order_item.menu_item:
            continue
        recipes = order_item.menu_item.recipes.select_related("inventory_item").all()
        for recipe in recipes:
            qty_recipe_unit = recipe.quantity_required * Decimal(str(order_item.quantity))
            try:
                qty_to_deduct = convert_quantity(qty_recipe_unit, recipe.unit, recipe.inventory_item.unit)
            except IncompatibleUnitsError as e:
                logger.error(
                    "[UNIT MISMATCH] Recipe %s (menu item '%s') -> inventory item '%s': %s. "
                    "Skipping this deduction — fix the recipe's unit.",
                    recipe.id, order_item.menu_item.name, recipe.inventory_item.name, e,
                )
                continue
            try:
                recipe.inventory_item.reduce_stock(
                    qty_to_deduct,
                    reference=f"Order #{order.id} — Token #{getattr(getattr(order, 'token', None), 'token_number', '?')}"
                )
                logger.info(
                    "Inventory | deducted %.3f %s of '%s' for Order #%s",
                    float(qty_to_deduct),
                    recipe.inventory_item.unit,
                    recipe.inventory_item.name,
                    order.id,
                )
            except DjangoValidationError as e:
                logger.warning(
                    "Inventory | insufficient stock for '%s' on Order #%s: %s",
                    recipe.inventory_item.name, order.id, e
                )
            except Exception as e:
                logger.error(
                    "Inventory | unexpected error deducting '%s' on Order #%s: %s",
                    recipe.inventory_item.name, order.id, e
                )