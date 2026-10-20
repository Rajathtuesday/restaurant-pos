"""
Selling a cover at the door, and spending it on a bill.

  * sell_cover: the door takes the money (cash, UPI or card) and gives the
    guest a slip with a code. The money is real takings on the night: a
    cash cover counts in the drawer when the shift closes.
  * redeem_cover: on the bill, the code pays as much of what is still due
    as the cover has left (a Payment with method "cover"). The bill itself
    doesn't change, so its tax doesn't either.
  * A cover can pay several bills (a group split over two tables) until it
    runs out. It lapses at the end of the business day it was sold on.
    A bill cancelled later gives its part back.

Open question for the CA, written on the covers page: the GST treatment of a
cover's unused part when it lapses.

Run: python manage.py test covers
"""
import secrets
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.errors import UserError
from core.utils import get_business_date
from core.validators import NumberInputError, read_number
from covers.models import PAID_BY, Cover, CoverRedemption

CODE_LETTERS = "ACDEFGHJKLMNPQRTUVWXY34679"   # no 0/O, 1/I, 2/Z, 5/S, 8/B mix-ups
CODE_LENGTH = 5
MAX_COVER = Decimal("100000")
MAX_GUESTS = 50


class CoverError(UserError):
    """A cover Rasova won't sell or take; says why."""


def _new_code():
    return "".join(secrets.choice(CODE_LETTERS) for _ in range(CODE_LENGTH))


def sell_cover(user, outlet, amount, paid_by, guests=1, guest_name="", guest_phone=""):
    try:
        amount = read_number(amount, "The cover amount", minimum=Decimal("1"), maximum=MAX_COVER, places=2)
    except NumberInputError as e:
        raise CoverError(e.message)
    if paid_by not in dict(PAID_BY):
        raise CoverError("Say how the cover was paid: cash, UPI or card.")
    try:
        guests = int(str(guests).strip() or "1")
    except ValueError:
        raise CoverError("Guests must be a whole number.")
    if not 1 <= guests <= MAX_GUESTS:
        raise CoverError(f"Guests must be from 1 to {MAX_GUESTS}.")
    phone = "".join(ch for ch in str(guest_phone or "") if ch.isdigit())[-10:]
    for _ in range(10):
        try:
            with transaction.atomic():
                return Cover.objects.create(
                    tenant=outlet.tenant, outlet=outlet, code=_new_code(), amount=amount, guests=guests,
                    guest_name=str(guest_name or "").strip()[:100], guest_phone=phone, paid_by=paid_by,
                    business_date=get_business_date(timezone.now(), outlet), sold_by=user,
                )
        except IntegrityError:
            continue        # the code was taken: try another
    raise CoverError("Could not make a cover code. Please try again.")


def redeem_cover(user, order, code):
    """Pay what is still due on `order` from the cover `code`, as far as it
    goes. Returns the CoverRedemption."""
    from orders.models import Order
    from orders.services.payment_service import process_payment

    code = "".join(str(code or "").upper().split())
    if not code:
        raise CoverError("Type the cover code from the slip.")
    with transaction.atomic():
        cover = (Cover.objects.select_for_update()
                 .filter(tenant=order.tenant, outlet=order.outlet, code=code).first())
        if cover is None:
            raise CoverError(f"No cover {code} at this outlet. Check the slip.")
        today = get_business_date(timezone.now(), order.outlet)
        if cover.business_date != today:
            raise CoverError(f"Cover {code} was for {cover.business_date:%d %b}. A cover is good only on its night.")
        balance = cover.balance()
        if balance <= 0:
            raise CoverError(f"Cover {code} is used up.")
        order = Order.objects.select_for_update().get(id=order.id)
        if order.status not in ("open", "billing"):
            raise CoverError("This bill is already settled.")
        paid = order.payments.exclude(method="refund").aggregate(total=Sum("amount"))["total"] or Decimal("0")
        due = order.grand_total - paid
        if due <= 0:
            raise CoverError("Nothing is left to pay on this bill.")
        amount = min(balance, due)
        result = process_payment(order, "cover", amount, user=user)
        return CoverRedemption.objects.create(
            cover=cover, order=order, payment=result["payment"], amount=result["payment"].amount, created_by=user,
        )


def night_summary(tenant, outlet, business_date):
    """The covers sold on one night, each with what was spent and what is
    left, and the totals: sold by how it was paid, spent, and left (which
    lapses when the night is over)."""
    covers = list(Cover.objects.filter(tenant=tenant, outlet=outlet, business_date=business_date)
                  .select_related("sold_by").prefetch_related("redemptions__order"))
    rows = []
    for cover in covers:
        used = sum((r.amount for r in cover.redemptions.all() if r.order.status != "cancelled"), Decimal("0"))
        rows.append({"cover": cover, "used": used, "left": cover.amount - used})
    sold_by_method = {key: sum((r["cover"].amount for r in rows if r["cover"].paid_by == key), Decimal("0"))
                      for key, _ in PAID_BY}
    return {
        "rows": rows,
        "guests": sum(r["cover"].guests for r in rows),
        "sold": sum((r["cover"].amount for r in rows), Decimal("0")),
        "sold_by_method": sold_by_method,
        "used": sum((r["used"] for r in rows), Decimal("0")),
        "left": sum((r["left"] for r in rows), Decimal("0")),
    }


def cover_takings(tenant, outlet, start, end):
    """Money taken for covers sold between two moments, by how it was paid:
    {"cash": ..., "upi": ..., "card": ...}. For closing a shift."""
    rows = (Cover.objects.filter(tenant=tenant, outlet=outlet, sold_at__gte=start, sold_at__lte=end)
            .values("paid_by").annotate(total=Sum("amount")))
    totals = {key: Decimal("0") for key, _ in PAID_BY}
    for row in rows:
        totals[row["paid_by"]] = row["total"] or Decimal("0")
    return totals
