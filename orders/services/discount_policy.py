"""
Who may give how much away, and the reason they must leave.

Every way staff can take money off a bill goes through here: a bill
discount (bill screen or the order API), a dish discount (bill screen or a
cart line), and making a dish free. A promo is not one of them: the owner or
a manager set it up in advance, so it needs no reason and no limit.

  * A manual discount needs a reason (it goes on the discount audit report).
  * A cashier or captain may give at most the outlet's staff discount limit,
    as a percent of the bill; a free dish counts as 100%. Above it, a
    manager or the owner has to apply it. With no limit set (the default)
    nothing changes from before: their authority stays uncapped, as agreed
    when the captain role was added.

Run: python manage.py test orders.tests.test_discount_hardening
"""
from decimal import Decimal

from orders.exceptions import OrderError

MANAGERS = ("owner", "manager")
REASON_MIN_LENGTH = 3
REASON_MAX_LENGTH = 200


class DiscountRefused(OrderError):
    """A discount given without what it needs (a reason). The message is
    written for the staff member and shown as it is."""


class DiscountNeedsManager(DiscountRefused):
    """A discount above this person's limit."""


def staff_limit(user, outlet):
    """The most this user may take off on their own, as a percent of the
    bill. None means no limit (owners, managers, superusers, and everyone
    when the outlet has no limit set)."""
    if getattr(user, "is_superuser", False) or getattr(user, "role", None) in MANAGERS:
        return None
    return outlet.staff_discount_limit_pct


ASK_DISCOUNT_REASON = "Give a reason for the discount (for example: regular guest, food complaint)."


def read_reason(raw, missing=ASK_DISCOUNT_REASON):
    """The reason as one tidy line, or DiscountRefused(missing) if there isn't one."""
    reason = " ".join(str(raw or "").split())
    if len(reason) < REASON_MIN_LENGTH:
        raise DiscountRefused(missing)
    return reason[:REASON_MAX_LENGTH]


def read_discount(raw, what="The discount", minimum=0):
    """A discount figure staff typed (a percent or an amount): a real number
    with at most 2 decimals, 0 or more unless `minimum` is None.
    DiscountRefused otherwise, with what to fix."""
    from core.validators import NumberInputError, read_number
    from orders.models import Order
    try:
        return read_number(raw, what, minimum=minimum, blank=Decimal("0"),
                           field=Order._meta.get_field("discount_value"))
    except NumberInputError as e:
        raise DiscountRefused(str(e))


def percent_of(amount, subtotal):
    """A flat amount as a percent of the bill it comes off. Anything off a
    zero bill counts as 100%."""
    amount, subtotal = Decimal(amount), Decimal(subtotal)
    if subtotal <= 0:
        return Decimal("100") if amount > 0 else Decimal("0")
    return amount * 100 / subtotal


def _shown(percent):
    text = f"{Decimal(percent):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def check_within_limit(user, outlet, percent, what="A discount"):
    """DiscountNeedsManager if `percent` is above this user's limit."""
    limit = staff_limit(user, outlet)
    if limit is not None and Decimal(percent) > limit:
        raise DiscountNeedsManager(
            f"{what} above {_shown(limit)}% needs a manager. Ask a manager to apply it."
        )


def bill_discount_percent(discount_type, value, subtotal):
    """How big a bill discount is, as a percent of the bill."""
    return Decimal(value) if discount_type == "percentage" else percent_of(value, subtotal)
