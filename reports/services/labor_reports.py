# reports/services/labor_reports.py
"""
Labor cost report -- what did staffing actually cost against what was
scheduled/clocked, and what fraction of revenue is going to labor.

Cost is derived from shifts.models.StaffPayRate, not ShiftTemplate.base_pay
(a flat per-template amount with no link to actual clocked hours). Two real
pay structures coexist in Indian restaurants and are handled explicitly:

  hourly  -- rate x actual clocked hours (Shift.duration_hours), matching
             how an hourly worker is actually paid.
  monthly -- the salary prorated across the calendar days the report period
             covers, month by month (so a period spanning a month boundary
             is still exact).

A staff member with no StaffPayRate row on file gets cost_known=False and is
excluded from total_labor_cost entirely -- never silently treated as free.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

from core.utils import get_business_date_range
from reports.services.sales_reports import daily_sales
from shifts.models import Shift, StaffPayRate


def _prorate_monthly_salary(monthly_salary, start_date, end_date):
    """Sums monthly_salary * (days of [start_date, end_date] in that month /
    days in that month) for every calendar month the range touches."""
    total = Decimal("0")
    current = start_date
    while current <= end_date:
        days_in_month = calendar.monthrange(current.year, current.month)[1]
        month_end = date(current.year, current.month, days_in_month)
        segment_end = min(end_date, month_end)
        days_in_segment = (segment_end - current).days + 1
        total += monthly_salary * Decimal(days_in_segment) / Decimal(days_in_month)
        current = segment_end + timedelta(days=1)
    return total


def labor_cost_report(tenant, outlet=None, start_date=None, end_date=None):
    if not start_date or not end_date:
        return {"rows": [], "total_labor_cost": 0, "labor_cost_pct": 0, "staff_with_unknown_cost": 0}

    range_start, _ = get_business_date_range(start_date, outlet)
    _, range_end = get_business_date_range(end_date, outlet)

    shifts_qs = Shift.objects.filter(
        tenant=tenant,
        clocked_in_at__gte=range_start, clocked_in_at__lt=range_end,
        clocked_out_at__isnull=False,
    ).select_related("staff")
    if outlet:
        shifts_qs = shifts_qs.filter(outlet=outlet)

    # Sum hours + tips per staff member across all their shifts in the period.
    staff_totals = {}
    for shift in shifts_qs:
        entry = staff_totals.setdefault(
            shift.staff_id, {"staff": shift.staff, "hours": Decimal("0"), "tips": Decimal("0")}
        )
        entry["hours"] += Decimal(str(shift.duration_hours or 0))
        entry["tips"] += shift.tips or Decimal("0")

    pay_rates = {
        r.staff_id: r
        for r in StaffPayRate.objects.filter(tenant=tenant, staff_id__in=staff_totals.keys())
    }

    rows = []
    total_labor_cost = Decimal("0")
    staff_with_unknown_cost = 0

    for staff_id, entry in staff_totals.items():
        rate = pay_rates.get(staff_id)
        cost_known = rate is not None

        if cost_known:
            if rate.pay_type == "hourly":
                base_cost = rate.hourly_rate * entry["hours"]
            else:
                base_cost = _prorate_monthly_salary(rate.monthly_salary, start_date, end_date)
            cost = base_cost + entry["tips"]
            total_labor_cost += cost
        else:
            staff_with_unknown_cost += 1
            cost = None

        rows.append({
            "staff_id": staff_id,
            "username": entry["staff"].username,
            "hours": round(float(entry["hours"]), 2),
            "tips": round(float(entry["tips"]), 2),
            "pay_type": rate.pay_type if rate else None,
            "cost_known": cost_known,
            "cost": round(float(cost), 2) if cost_known else None,
        })

    rows.sort(key=lambda r: r["cost"] or 0, reverse=True)

    sales = daily_sales(tenant, outlet, start_date, end_date)
    revenue = float(sales.get("total_sales", 0) or 0)
    total_labor_cost_float = float(total_labor_cost)
    labor_cost_pct = round(total_labor_cost_float / revenue * 100, 1) if revenue > 0 else 0

    return {
        "rows": rows,
        "total_labor_cost": round(total_labor_cost_float, 2),
        "labor_cost_pct": labor_cost_pct,
        "staff_with_unknown_cost": staff_with_unknown_cost,
        "revenue": round(revenue, 2),
    }
