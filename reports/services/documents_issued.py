"""
GSTR-1 Table 13, documents issued (P23): for each series of bill numbers in
the return's period, the first and the last number, how many bills, and how
many of them were cancelled. It can't be left blank since the May 2025
returns.

A bill belongs to the period of its business day, as in every report (P16).
Its number was given when it was billed (orders/services/bill_numbers.py),
so the series holds no unused numbers. A bill opened at the end of one
period and billed after the next began has a number among the next period's
bills, which is why Total counts the bills, not the numbers from first to
last. Old-style numbers (INV-...) were also taken by orders cancelled before
their bill, so those series can skip numbers.
"""
from core.utils import get_business_period
from orders.models import Order
from orders.services.bill_numbers import split_bill_number

NATURE = "Invoices for outward supply"


def series_rows(bills):
    """bills: (bill_number, status) pairs. One row per series, sorted by
    series: the first and last bill number, the bills, the cancelled ones and
    the ones that stand."""
    series = {}
    for number, status in bills:
        parts = split_bill_number(number)
        if parts:
            name, serial = parts
            series.setdefault(name, {})[serial] = (number, status)
    rows = []
    for name in sorted(series):
        bills_in_series = series[name]
        first, last = min(bills_in_series), max(bills_in_series)
        cancelled = sum(1 for _, status in bills_in_series.values() if status == "cancelled")
        rows.append({
            "series": name,
            "first": bills_in_series[first][0],
            "last": bills_in_series[last][0],
            "total": len(bills_in_series),
            "cancelled": cancelled,
            "net": len(bills_in_series) - cancelled,
        })
    return rows


def documents_issued(tenant, outlet, start_date, end_date):
    """Table 13's rows for the business days start_date to end_date."""
    start, end = get_business_period(start_date, end_date, outlet)
    bills = Order.objects.filter(
        tenant=tenant, created_at__gte=start, created_at__lt=end, bill_number__isnull=False,
    )
    if outlet:
        bills = bills.filter(outlet=outlet)
    return series_rows(bills.values_list("bill_number", "status"))
