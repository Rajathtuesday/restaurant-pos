"""
A bill never asks for less than the guest has already paid on it
(21 Nov 2026).

Found in the pub: a cover paid part of a bill, then a dish was taken off and
a promo put on, and the bill came to less than the cover had paid. Nothing
stopped it: voids, discounts and promos looked only at whether the bill was
settled, not at what had been paid. The bill screen then said "FULLY PAID"
with nothing to give back, the cover counted the whole payment as spent, and
the bill could not be closed.

So Order.recalculate_totals, the one place every change to a bill ends,
calls keep_bill_above_paid. When the new total is below what is paid:

  1. Covers shrink first. A cover pays only what is due (covers/services.py),
     so its payment comes down to the new total and the difference is back
     on the cover for the night. No money moves.
  2. Money paid (cash, UPI, card...) cannot shrink by itself: the guest gets
     the difference back. The outlet chooses how (Outlet.bill_below_paid,
     Setup, Outlet Settings):
       give_back     a manager gives it back on the spot: the change goes
                     through with a manager's PIN (or done by a manager), and
                     the give-back is recorded with it (payments/
                     refund_service.give_back). Without one, GiveBackNeeded,
                     which the screen turns into the PIN prompt.
       refund_first  the change is refused (BillBelowPaid) until someone
                     gives the difference back from the bill screen.

The caller's transaction holds it all: a refused change leaves nothing
behind. Who is making the change comes from acting_as() (services, tests) or
the request (core.middleware.CurrentRequestMiddleware), with the manager's
PIN as read by orders.services.approvals.read_approval.
Run: python manage.py test covers.tests.test_bill_drops_below_paid
"""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal

from orders.exceptions import OrderError
from orders.services import approvals

current_request = ContextVar("rasova_current_request", default=None)
_acting = ContextVar("rasova_paid_guard_acting", default=None)
_UNREAD = object()


class BillBelowPaid(OrderError):
    """The change would leave less to pay than has been paid, and the outlet
    gives back first; the message says how much."""


class GiveBackNeeded(approvals.ApprovalNeeded):
    """The change can go through with a manager giving the difference back."""


@contextmanager
def acting_as(user, approver=None):
    """Who makes the changes inside this block, for callers without a
    request (a service, a test)."""
    token = _acting.set((user, approver))
    try:
        yield
    finally:
        _acting.reset(token)


def _who(order, message):
    """The manager who approves giving money back: the person making the
    change if they may, or the manager whose PIN came with the request.
    GiveBackNeeded(message) when neither."""
    acting = _acting.get()
    if acting is not None:
        user, approver = acting
    else:
        request = current_request.get()
        if request is None:
            raise GiveBackNeeded(message)
        user = getattr(request, "user", None)
        approver = getattr(request, "_rasova_approver", _UNREAD)
        if approver is _UNREAD:
            approver = approvals.read_approval(request, order.outlet)
    if user is not None and approvals.approves_own(user):
        return user
    if approver is not None:
        return approver
    raise GiveBackNeeded(message)


def _money(value):
    return f"₹{value.quantize(Decimal('0.01'))}"


def _shrink_covers(order, over):
    """Take up to `over` off this bill's cover payments, latest first; the
    rest of each cover stays spendable tonight. Returns what is still over."""
    from covers.models import CoverRedemption
    from orders.models import OrderEvent
    redemptions = (CoverRedemption.objects.select_for_update().filter(order=order)
                   .select_related("payment", "cover").order_by("-created_at", "-id"))
    for redemption in redemptions:
        if over <= 0:
            break
        cut = min(over, redemption.amount)
        before = redemption.amount
        payment = redemption.payment
        if cut == before:
            redemption.delete()
            payment.delete()
        else:
            redemption.amount = before - cut
            redemption.save(update_fields=["amount"])
            payment.amount = before - cut
            payment.save(update_fields=["amount"])
        OrderEvent.objects.create(
            tenant=order.tenant, outlet=order.outlet, order=order, event_type="payment_refunded", amount=cut,
            metadata={"to": "cover", "cover": redemption.cover.code, "before": str(before),
                      "after": str(before - cut), "why": "the bill came down below what was paid"},
        )
        over -= cut
    return over


def _pending_refunds(order):
    """Refunds asked for on this bill and not yet decided: on their way back
    to the guest already, so not given back a second time."""
    from django.db.models import Sum

    from payments.models import Refund
    return (Refund.objects.filter(order=order, status="pending").aggregate(total=Sum("amount"))["total"]
            or Decimal("0"))


def keep_bill_above_paid(order):
    """Called by Order.recalculate_totals after it saves the new total."""
    if order.status not in ("open", "billing"):
        return
    over = order.amount_paid() - _pending_refunds(order) - order.grand_total
    if over <= 0:
        return
    over = _shrink_covers(order, over)
    if over <= 0:
        return

    paid = order.amount_paid() - _pending_refunds(order)
    rule = getattr(order.outlet, "bill_below_paid", "give_back")
    if rule == "refund_first":
        raise BillBelowPaid(
            f"{_money(paid)} is already paid on this bill, and this change would make it "
            f"{_money(order.grand_total)}. Give back {_money(over)} first (Give back, on the bill), "
            f"then make the change.")
    who = _who(order, (
        f"{_money(paid)} is already paid on this bill, and this change would make it "
        f"{_money(order.grand_total)}. A manager gives back {_money(over)}."))
    from payments.refund_service import give_back
    give_back(order, over, who, reason=f"Bill came down to {_money(order.grand_total)} after payment")
