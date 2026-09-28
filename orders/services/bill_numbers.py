"""
Bill numbers (P18). A tax invoice needs a serial number of at most 16
characters (letters, digits, "-" and "/"), unique within the financial year
(CGST Rules, rule 46(b)). Rasova numbers each outlet's bills in one series a
financial year:

    SG/2627/000123
    |  |    the bill's place in its series, from 000001
    |  the financial year: April 2026 to March 2027
    the outlet's code (Outlet.bill_code): up to 3 letters or digits,
    different for each outlet of a restaurant

That is at most 3 + 1 + 4 + 1 + 7 = 16 characters for up to 9,999,999 bills
an outlet a year. An order gets its number the first time it is billed: the
Bill button, a payment, or an order that arrives already paid. Never when it
is opened, so an order cancelled before its bill leaves no gap in the series,
and a bill cancelled after it keeps its number (a cancelled invoice). The
year is the one of the order's business day, the day the reports count it on.

Bills issued before bill numbers came in keep the order number they were
printed with: a migration copied it into Order.bill_number.
"""
import re

from core.utils import get_business_date

# Statuses of a bill that has been presented or settled.
BILLED = ("billing", "paid", "closed")

# A bill number: its series, then its place in the series after the last "/"
# (SG/2627/000123) or "-" (the old INV-6-20260928-0001, one series a day).
_NUMBER = re.compile(r"^(?P<series>.+)[/-](?P<serial>\d+)$")


def split_bill_number(number):
    """The series and the place in it: ("SG/2627", 123) for SG/2627/000123,
    ("INV-6-20260928", 1) for an old INV-6-20260928-0001. None for anything
    else."""
    match = _NUMBER.match(number or "")
    return (match["series"], int(match["serial"])) if match else None


def financial_year(day):
    """The financial year a day falls in, as four digits: "2627" for any
    day from 1 April 2026 to 31 March 2027."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def series_prefix(outlet, day):
    """The start of every bill number in the outlet's series for the
    financial year of `day`: "SG/2627"."""
    return f"{outlet.ensure_bill_code()}/{financial_year(day)}"


def next_bill_number(order):
    """The next number in the series of the order's outlet, for the financial
    year of the order's business day. Call it inside a transaction: the
    series row stays locked until the transaction ends, so no two bills ever
    get the same number and none is skipped."""
    from orders.models import BillSeries

    prefix = series_prefix(order.outlet, get_business_date(order.created_at, order.outlet))
    series, _ = (
        BillSeries.objects.for_outlet(order.tenant, order.outlet)   # whatever the request's tenant
        .select_for_update()
        .get_or_create(tenant=order.tenant, outlet=order.outlet, prefix=prefix)
    )
    series.last_number += 1
    series.save(update_fields=["last_number"])
    return f"{prefix}/{series.last_number:06d}"
