"""
Who may take money off a printed bill, and how a cashier gets that approval.

Once the bill is printed (order status "billing", bill number issued) the
guest has seen the total. Lowering it after that is where a till loses
money: a dish voided after the guest ate it, a quantity cut, the whole order
cancelled, a discount or a free dish added, the parcel charge taken off, the
order re-tagged as Zomato so its GST drops. Each of those needs a manager:

  * a manager or the owner doing it approves it by doing it;
  * anyone else needs a manager's (or the owner's) approval PIN, typed on
    their own screen. The server answers 403 {"needs_approval": true, ...};
    the browser (static/js/approval.js) asks for the PIN and sends the same
    request again with the headers X-Approver-Id and X-Approver-Pin.

The same approval is needed to throw away a dish the kitchen already made
(wastage), before the bill as well as after, and to cancel a served dish.

Every approved change to a printed bill is recorded as a "changed_after_bill"
event: the bill before and after, who did it, and whose PIN approved it. The
Discount & Void audit lists them (reports/services/audit_reports.py).

PINs are 4 to 6 digits, hashed like a password, and lock for PIN_LOCK after
PIN_TRIES wrong tries in a row.

Run: python manage.py test orders.tests.test_printed_bill_approval
"""
import math
import re
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone

from core.errors import UserError
from orders.exceptions import OrderError

APPROVER_ROLES = ("owner", "manager")
PIN_TRIES = 5
PIN_LOCK = timedelta(minutes=15)
PIN_PATTERN = re.compile(r"^\d{4,6}$")
EASY_PINS = frozenset({"1234", "12345", "123456", "4321", "54321", "654321", "0123", "01234", "012345"})

APPROVER_ID_HEADER = "X-Approver-Id"
APPROVER_PIN_HEADER = "X-Approver-Pin"

# What the screen says when it asks for a PIN, by the reason one is needed.
PRINTED = "The bill has been printed. A manager's PIN is needed to {what}."
MADE = "This was already made, so cancelling it is wastage. A manager's PIN is needed."
SERVED = "This was already served. A manager's PIN is needed to cancel it."


class ApprovalNeeded(OrderError):
    """Allowed, but only with a manager's approval."""


class ApprovalRefused(OrderError):
    """A PIN came with the request and did not approve it."""


class PinError(UserError):
    """A PIN someone tried to set that Rasova won't accept; says why."""


def approves_own(user):
    """A manager, the owner or a superuser approves what they do themselves."""
    return bool(getattr(user, "is_superuser", False) or getattr(user, "role", None) in APPROVER_ROLES)


def require(user, approver, message):
    """
    Who approves this change: `user` themselves when they may, otherwise the
    manager whose PIN came with the request. ApprovalNeeded(message) if
    neither.
    """
    if approves_own(user):
        return user
    if approver is not None:
        return approver
    raise ApprovalNeeded(message)


def printed_bill(order):
    """True once the bill has been printed and before it is paid."""
    return order.status == "billing"


def approvers(tenant, outlet):
    """Who can approve at this outlet: its managers and the tenant's owners,
    active, with a PIN set."""
    from accounts.models import User
    return (
        User.objects.filter(tenant=tenant, is_active=True)
        .filter(Q(role="owner") | Q(role="manager", outlet=outlet))
        .exclude(approval_pin="")
    )


def _name(user):
    return (user.get_full_name() or user.username).strip()


def read_approval(request, outlet):
    """
    The manager whose PIN came with this request, None if none came, or
    ApprovalRefused if one came and doesn't approve.

    Call it before the change's own transaction: a wrong try is saved in a
    transaction of its own here, so it still counts when the change is then
    refused.
    """
    raw_id = (request.headers.get(APPROVER_ID_HEADER) or "").strip()
    pin = (request.headers.get(APPROVER_PIN_HEADER) or "").strip()
    if not raw_id and not pin:
        return None
    if not raw_id.isdigit() or not pin:
        raise ApprovalRefused("Pick the manager and type their PIN.")
    return check_pin(int(raw_id), pin, request.user.tenant, outlet)


def check_pin(approver_id, pin, tenant, outlet, now=None):
    """The approver if `pin` is theirs; ApprovalRefused otherwise."""
    now = now or timezone.now()
    with transaction.atomic():
        approver = approvers(tenant, outlet).select_for_update().filter(id=approver_id).first()
        if approver is None:
            refusal = "That person can't approve here."
        elif approver.approval_pin_locked_until and approver.approval_pin_locked_until > now:
            minutes = math.ceil((approver.approval_pin_locked_until - now).total_seconds() / 60)
            refusal = (f"{_name(approver)}'s PIN is locked after {PIN_TRIES} wrong tries. "
                       f"Try again in {minutes} min.")
        elif check_password(pin, approver.approval_pin):
            if approver.approval_pin_failures or approver.approval_pin_locked_until:
                approver.approval_pin_failures = 0
                approver.approval_pin_locked_until = None
                approver.save(update_fields=["approval_pin_failures", "approval_pin_locked_until"])
            return approver
        else:
            approver.approval_pin_failures += 1
            if approver.approval_pin_failures >= PIN_TRIES:
                approver.approval_pin_failures = 0
                approver.approval_pin_locked_until = now + PIN_LOCK
                refusal = (f"Wrong PIN. {_name(approver)}'s PIN is now locked for "
                           f"{int(PIN_LOCK.total_seconds() // 60)} minutes.")
            else:
                left = PIN_TRIES - approver.approval_pin_failures
                refusal = f"Wrong PIN. {left} {'try' if left == 1 else 'tries'} left."
            approver.save(update_fields=["approval_pin_failures", "approval_pin_locked_until"])
    # Raised outside the block, so the count above is saved.
    raise ApprovalRefused(refusal)


def set_pin(user, pin):
    """Set `user`'s approval PIN; PinError if it isn't one Rasova accepts."""
    pin = str(pin or "").strip()
    if not PIN_PATTERN.match(pin):
        raise PinError("A PIN is 4 to 6 digits.")
    if len(set(pin)) == 1 or pin in EASY_PINS:
        raise PinError("That PIN is too easy to guess. Pick another.")
    user.approval_pin = make_password(pin)
    user.approval_pin_failures = 0
    user.approval_pin_locked_until = None
    user.save(update_fields=["approval_pin", "approval_pin_failures", "approval_pin_locked_until"])


def clear_pin(user):
    user.approval_pin = ""
    user.approval_pin_failures = 0
    user.approval_pin_locked_until = None
    user.save(update_fields=["approval_pin", "approval_pin_failures", "approval_pin_locked_until"])


def approval_response(e, tenant, outlet):
    """The 403 the browser turns into a PIN prompt."""
    from django.urls import reverse
    people = approvers(tenant, outlet).order_by("role", "first_name", "username")
    return JsonResponse({
        "error": e.message,
        "needs_approval": True,
        "refused": isinstance(e, ApprovalRefused),
        "approvers": [{"id": u.id, "name": _name(u), "role": u.role} for u in people],
        "set_pin_url": reverse("my_approval_pin"),
    }, status=403)


def record_after_bill(order, user, approved_by, what, before, extra=None):
    """An approved change to a printed bill, for the audit."""
    from orders.models import OrderEvent
    order.refresh_from_db(fields=["grand_total"])
    OrderEvent.objects.create(
        tenant=order.tenant, outlet=order.outlet, order=order,
        event_type="changed_after_bill", created_by=user,
        metadata={
            "what": what,
            "before": str(before),
            "after": str(order.grand_total),
            "approved_by": approved_by.username,
            "approved_by_id": approved_by.id,
            **(extra or {}),
        },
    )


def lowered_printed_bill(order, user, approver, what, before):
    """
    Call after a change has re-totalled `order`. If the bill was printed and
    the change lowered it, who approved that; ApprovalNeeded if nobody did
    (raised inside the change's transaction, so the change is undone). None
    when no approval was needed.
    """
    if not printed_bill(order) or order.grand_total >= before:
        return None
    return require(user, approver, PRINTED.format(what=what))
