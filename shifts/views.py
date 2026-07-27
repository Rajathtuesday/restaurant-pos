# shifts/views.py
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib import messages

from core.decorators import tenant_required, role_required
from .models import Shift


@login_required
@tenant_required
def shift_dashboard(request):
    """Manager/Owner sees all shifts. Staff sees their own."""
    tenant = request.user.tenant
    outlet = request.user.outlet
    from django.utils.timezone import localdate
    from datetime import timedelta

    date_str = request.GET.get("date")
    if date_str:
        try:
            from datetime import datetime
            view_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            view_date = localdate()
    else:
        view_date = localdate()

    if request.user.role in ("manager", "owner") or request.user.is_superuser:
        shifts = Shift.objects.filter(
            tenant=tenant, outlet=outlet,
            clocked_in_at__date=view_date
        ).select_related("staff").order_by("clocked_in_at")
    else:
        shifts = Shift.objects.filter(
            tenant=tenant, outlet=outlet,
            staff=request.user,
            clocked_in_at__date=view_date
        ).order_by("clocked_in_at")

    # Check if current user has active shift
    active_shift = Shift.objects.filter(
        tenant=tenant, outlet=outlet, staff=request.user, clocked_out_at__isnull=True
    ).first()

    return render(request, "shifts/shift_dashboard.html", {
        "shifts": shifts,
        "active_shift": active_shift,
        "view_date": view_date,
    })


@login_required
@tenant_required
@require_POST
def clock_in(request):
    """
    Clocks in the current staff user. 
    Verifies they do not already have an active shift.
    """
    tenant = request.user.tenant
    outlet = request.user.outlet

    # Check if already clocked in
    active = Shift.objects.filter(
        tenant=tenant, outlet=outlet,
        staff=request.user, clocked_out_at__isnull=True
    ).first()

    if active:
        return JsonResponse({"error": "Already clocked in"}, status=400)

    shift = Shift.objects.create(
        tenant=tenant,
        outlet=outlet,
        staff=request.user,
        clocked_in_at=timezone.now()
    )
    return JsonResponse({"success": True, "shift_id": shift.id, "clocked_in_at": shift.clocked_in_at.isoformat()})


@login_required
@tenant_required
@require_POST
def clock_out(request):
    """
    Clocks out the current staff user and optionally handles shift reporting payload.
    Auto-calculates the duration of the shift.
    """
    tenant = request.user.tenant
    outlet = request.user.outlet

    shift = Shift.objects.filter(
        tenant=tenant, outlet=outlet,
        staff=request.user, clocked_out_at__isnull=True
    ).first()

    if not shift:
        return JsonResponse({"error": "No active shift found"}, status=400)

    try:
        data = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)

    shift.clocked_out_at = timezone.now()
    shift.tips = data.get("tips", 0) or 0
    shift.notes = data.get("notes", "") or ""
    shift.save(update_fields=["clocked_out_at", "tips", "notes"])

    # Generate Shift Summary
    from orders.models import Payment
    from django.db.models import Sum, Q
    
    sales = Payment.objects.filter(
        order__tenant=request.user.tenant,
        created_by=request.user,
        paid_at__gte=shift.clocked_in_at,
        paid_at__lte=shift.clocked_out_at
    ).aggregate(
        total=Sum("amount"),
        cash=Sum("amount", filter=Q(method="cash")),
        digital=Sum("amount", filter=Q(method__in=["upi", "card"]))
    )

    return JsonResponse({
        "success": True,
        "duration_hours": shift.duration_hours,
        "clocked_out_at": shift.clocked_out_at.isoformat(),
        "summary": {
            "total_sales": float(sales["total"] or 0),
            "cash": float(sales["cash"] or 0),
            "digital": float(sales["digital"] or 0),
            "tips": float(shift.tips)
        }
    })


@login_required
@tenant_required
@role_required("manager", "owner")
@require_POST
def update_shift_tips(request, shift_id):
    """Manager can update tips for any shift."""
    try:
        data = json.loads(request.body)
        # Scope by outlet — a manager at outlet A must not edit outlet B's shift.
        shift = Shift.objects.get(
            id=shift_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
        shift.tips = data.get("tips", 0)
        shift.save(update_fields=["tips"])
        return JsonResponse({"success": True})
    except Shift.DoesNotExist:
        return JsonResponse({"error": "Shift not found"}, status=404)


@login_required
@tenant_required
@role_required("manager", "owner")
def cash_session_list(request):
    """
    Renders the Cash Sessions dashboard (Cash Register Management).
    Shows the active session and historical sessions for the current outlet.
    Managers/Owners only.
    """
    from .models import CashSession
    sessions = CashSession.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).order_by("-opened_at")

    active_session = sessions.filter(status="open").first()

    return render(request, "shifts/cash_sessions.html", {
        "sessions": sessions,
        "active_session": active_session
    })


@login_required
@tenant_required
@require_POST
def open_cash_session(request):
    """
    Opens a new Cash Register session (Till).
    Requires a starting float/opening balance.
    Prevents opening multiple sessions per user/outlet simultaneously.
    """
    from .models import CashSession
    from django.db import transaction

    try:
        data = json.loads(request.body)
        from decimal import Decimal
        opening_balance = Decimal(str(data.get("opening_balance", "0")))

        with transaction.atomic():
            # Lock to prevent race condition between two managers
            existing = CashSession.objects.select_for_update().filter(
                tenant=request.user.tenant,
                outlet=request.user.outlet,
                status="open"
            ).first()

            if existing:
                return JsonResponse({"error": "A session is already open"}, status=400)

            session = CashSession.objects.create(
                tenant=request.user.tenant,
                outlet=request.user.outlet,
                opened_by=request.user,
                opening_balance=opening_balance,
                status="open"
            )

        return JsonResponse({"success": True, "session_id": session.id})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
@require_POST
def close_cash_session(request):
    """Close active session and reconcile totals."""
    from .models import CashSession
    from orders.models import Payment, Order
    from payments.models import Refund
    from django.db.models import Sum

    try:
        data = json.loads(request.body)
        from decimal import Decimal
        actual_cash = Decimal(str(data.get("actual_cash", "0")))

        session = CashSession.objects.filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            status="open"
        ).first()

        if not session:
            return JsonResponse({"error": "No open session found"}, status=400)

        close_time = timezone.now()

        # 1. Calculate Expected Cash (Opening + All Cash Payments since opened_at)
        cash_payments = Payment.objects.filter(
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet,
            method="cash",
            paid_at__gte=session.opened_at,
            paid_at__lte=close_time
        ).aggregate(total=Sum("amount"))["total"] or 0

        # A cash refund physically leaves the drawer, but approve_refund records
        # the reversing entry as method="refund" (never "cash") so the sum above
        # doesn't subtract it — which used to manufacture a phantom shortfall the
        # cashier got blamed for. Find the refund reversing entries in this
        # window, then subtract only those whose ORIGINAL payment was cash
        # (a refund against a card/UPI sale didn't come out of the drawer).
        window_refund_refs = Payment.objects.filter(
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet,
            method="refund",
            paid_at__gte=session.opened_at,
            paid_at__lte=close_time,
        ).values_list("reference", flat=True)
        refund_ids = [
            ref.split("REFUND-")[1]
            for ref in window_refund_refs
            if ref and ref.startswith("REFUND-")
        ]
        cash_refund_total = Decimal("0")
        if refund_ids:
            cash_refund_total = Refund.objects.filter(
                id__in=refund_ids, payment__method="cash",
            ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

        expected_cash = (
            Decimal(str(session.opening_balance))
            + Decimal(str(cash_payments or 0))
            - Decimal(str(cash_refund_total or 0))
        )

        # 2. Calculate Digital Payments
        digital_payments = Payment.objects.filter(
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet,
            method__in=["upi", "card"],
            paid_at__gte=session.opened_at,
            paid_at__lte=close_time
        ).aggregate(total=Sum("amount"))["total"] or 0

        # 3. Total Sales (Grand total of orders paid in this window)
        total_sales = Decimal(str(cash_payments or 0)) + Decimal(str(digital_payments or 0))

        session.closed_at = close_time
        session.closed_by = request.user
        session.expected_cash = expected_cash
        session.actual_cash = actual_cash
        session.discrepancy = actual_cash - expected_cash
        session.total_digital_payments = digital_payments
        session.total_sales = total_sales
        session.status = "closed"
        session.save()

        return JsonResponse({"success": True, "discrepancy": float(session.discrepancy)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
def export_z_report(request):
    """Exports Z-report for the day including summary and session details."""
    import csv
    from django.http import HttpResponse
    from django.db.models import Sum, Q
    from .models import CashSession
    from orders.models import Payment, Order

    from core.utils import get_business_date, get_business_date_range

    outlet = request.user.outlet
    business_date = get_business_date(timezone.now(), outlet)
    start, end = get_business_date_range(business_date, outlet)

    # All three queries below key off this ONE order set instead of each
    # independently re-deriving "today" — previously orders were filtered
    # by created_at__date, payments by paid_at__date, and sessions by
    # opened_at__date, three different fields on three different plain
    # calendar-date filters that (a) ignored the outlet's business-day
    # cutoff entirely, so anything placed after midnight but before the
    # cutoff hour landed on the wrong day's report, and (b) weren't even
    # guaranteed to represent the same set of transactions as each other
    # (an order created just before midnight but paid just after would be
    # "today" in one query and "yesterday" in the other).
    orders_qs = Order.objects.filter(
        tenant=request.user.tenant,
        outlet=outlet,
        created_at__gte=start,
        created_at__lt=end,
        status__in=["paid", "closed"]
    )

    sessions = CashSession.objects.filter(
        tenant=request.user.tenant,
        outlet=outlet,
        opened_at__gte=start,
        opened_at__lt=end,
    ).order_by('opened_at')

    # Calculate Daily Summary
    payments = Payment.objects.filter(order__in=orders_qs)

    summary = payments.aggregate(
        total=Sum("amount"),
        cash=Sum("amount", filter=Q(method="cash")),
        digital=Sum("amount", filter=Q(method__in=["upi", "card"])),
        refunds=Sum("amount", filter=Q(method="refund"))
    )

    orders = orders_qs.aggregate(
        subtotal=Sum("subtotal"),
        discount=Sum("discount_total"),
        gst=Sum("gst_total"),
        round_off=Sum("round_off")
    )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="z_report_{business_date}.csv"'

    writer = csv.writer(response)

    writer.writerow(['DAILY Z-REPORT SUMMARY', business_date.strftime('%d %b %Y')])
    writer.writerow(['Outlet', request.user.outlet.name])
    writer.writerow([])
    
    writer.writerow(['FINANCIAL TOTALS'])
    writer.writerow(['Gross Subtotal', f"{orders['subtotal'] or 0:.2f}"])
    writer.writerow(['Total Discount', f"{orders['discount'] or 0:.2f}"])
    writer.writerow(['GST Collected', f"{orders['gst'] or 0:.2f}"])
    writer.writerow(['Round Off', f"{orders['round_off'] or 0:.2f}"])
    writer.writerow(['Net Refunds', f"{abs(summary['refunds'] or 0):.2f}"])
    writer.writerow(['NET REVENUE', f"{summary['total'] or 0:.2f}"])
    writer.writerow([])

    writer.writerow(['PAYMENT BREAKDOWN'])
    writer.writerow(['Cash', f"{summary['cash'] or 0:.2f}"])
    writer.writerow(['Digital (UPI/Card)', f"{summary['digital'] or 0:.2f}"])
    writer.writerow([])

    # Detailed Item Sales for the Day
    from django.db.models import Count, F, ExpressionWrapper, DecimalField
    from orders.models import OrderItem
    
    item_sales = OrderItem.objects.filter(
        order__in=orders_qs
    ).annotate(
        line_rev=ExpressionWrapper(
            F('total_price') * (1 - F('item_discount_pct') / 100),
            output_field=DecimalField()
        )
    ).values('menu_item__name').annotate(
        qty=Sum('quantity'),
        rev=Sum('line_rev')
    ).order_by('-qty')

    writer.writerow(['ITEM-WISE SALES'])
    writer.writerow(['Item Name', 'Quantity', 'Net Revenue'])
    for item in item_sales:
        writer.writerow([item['menu_item__name'], item['qty'], round(item['rev'], 2)])
    writer.writerow([])

    writer.writerow(['CASH SESSION DETAILS'])
    writer.writerow(['Session ID', 'Status', 'Opened At', 'Closed At', 'Opened By', 'Opening Bal', 'Exp Cash', 'Actual Cash', 'Diff', 'Cash Sales'])

    for s in sessions:
        writer.writerow([
            s.id,
            s.status.upper(),
            timezone.localtime(s.opened_at).strftime('%H:%M') if s.opened_at else '',
            timezone.localtime(s.closed_at).strftime('%H:%M') if s.closed_at else '-',
            s.opened_by.username if s.opened_by else '',
            s.opening_balance,
            s.expected_cash,
            s.actual_cash,
            s.discrepancy,
            s.cash_sales
        ])

    return response


# ==========================================================
#  SCHEDULE BUILDER
# ==========================================================

from .models import ShiftTemplate, StaffSchedule


@login_required
@tenant_required
@role_required("manager", "owner")
def schedule_builder(request):
    """
    Weekly schedule grid — managers only.
    Shows each staff member vs each day of the week.
    Supports ?week=YYYY-MM-DD to navigate to any Monday.
    """
    from datetime import timedelta, datetime
    from accounts.models import User

    # Resolve the Monday of the requested week
    week_str = request.GET.get("week")
    try:
        week_start = datetime.strptime(week_str, "%Y-%m-%d").date()
        # snap to Monday regardless of what date is passed
        week_start = week_start - timedelta(days=week_start.weekday())
    except (TypeError, ValueError):
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())

    week_days = [week_start + timedelta(days=i) for i in range(7)]
    week_end = week_days[-1]
    prev_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)

    tenant = request.user.tenant
    outlet = request.user.outlet

    staff_list = User.objects.filter(
        tenant=tenant, outlet=outlet, is_active=True
    ).exclude(role__in=["owner"]).order_by("first_name", "username")

    templates = ShiftTemplate.objects.filter(tenant=tenant, outlet=outlet).order_by("name")

    # Load all schedules for this week in one query
    schedules_qs = StaffSchedule.objects.filter(
        tenant=tenant,
        outlet=outlet,
        date__gte=week_start,
        date__lte=week_end,
        is_active=True
    ).select_related("staff", "template")

    # Build a lookup: {(staff_id, date): schedule}
    schedule_map = {}
    for sc in schedules_qs:
        schedule_map[(sc.staff_id, sc.date)] = sc

    # Also load actual shifts (clock-in records) for the week for the overtime badge
    actual_shifts = Shift.objects.filter(
        tenant=tenant, outlet=outlet,
        clocked_in_at__date__gte=week_start,
        clocked_in_at__date__lte=week_end
    ).select_related("staff")

    shift_map = {}
    for sh in actual_shifts:
        shift_map[(sh.staff_id, sh.clocked_in_at.date())] = sh

    # Build grid rows
    grid = []
    for staff in staff_list:
        row = {"staff": staff, "cells": []}
        for day in week_days:
            sched = schedule_map.get((staff.id, day))
            actual = shift_map.get((staff.id, day))
            overtime = None
            if actual and sched:
                overtime = actual.overtime_hours
            row["cells"].append({
                "day": day,
                "schedule": sched,
                "actual_shift": actual,
                "overtime": overtime,
            })
        grid.append(row)

    return render(request, "shifts/schedule_builder.html", {
        "grid": grid,
        "week_days": week_days,
        "week_start": week_start,
        "prev_week": prev_week,
        "next_week": next_week,
        "templates": templates,
        "staff_list": staff_list,
    })


@login_required
@tenant_required
@role_required("manager", "owner")
@require_POST
def create_schedule_entry(request):
    """
    Assigns a staff member to a shift on a date.

    Edge cases:
    - Duplicate: if a schedule already exists for staff+date, update it in-place.
    - Missing times: if no template and no start/end times supplied, reject.
    - Date in past: allowed (managers can back-fill missed schedules).
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    from accounts.models import User
    from datetime import date as date_type, datetime

    staff_id = data.get("staff_id")
    date_str = data.get("date")
    template_id = data.get("template_id")
    start_time_str = data.get("start_time")
    end_time_str = data.get("end_time")
    notes = data.get("notes", "")

    # --- Validation ---
    if not staff_id or not date_str:
        return JsonResponse({"error": "staff_id and date are required"}, status=400)

    try:
        sched_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return JsonResponse({"error": "Invalid date format (YYYY-MM-DD expected)"}, status=400)

    try:
        staff = User.objects.get(
            id=staff_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
    except User.DoesNotExist:
        return JsonResponse({"error": "Staff member not found"}, status=404)

    template = None
    if template_id:
        try:
            # Scope by outlet — otherwise another outlet's template (different
            # pay rate / hours) could be applied to this outlet's schedule.
            template = ShiftTemplate.objects.get(
                id=template_id, tenant=request.user.tenant, outlet=request.user.outlet
            )
        except ShiftTemplate.DoesNotExist:
            return JsonResponse({"error": "Shift template not found"}, status=404)

    # If no template, require explicit times
    if not template and (not start_time_str or not end_time_str):
        return JsonResponse(
            {"error": "Either a template or both start_time and end_time are required"},
            status=400
        )

    from datetime import time
    start_time = end_time = None
    if not template:
        try:
            start_time = datetime.strptime(start_time_str, "%H:%M").time()
            end_time = datetime.strptime(end_time_str, "%H:%M").time()
        except ValueError:
            return JsonResponse({"error": "Invalid time format (HH:MM expected)"}, status=400)

    # --- Upsert (update if exists, create if not) ---
    schedule, created = StaffSchedule.objects.update_or_create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        staff=staff,
        date=sched_date,
        defaults={
            "template": template,
            "start_time": start_time,
            "end_time": end_time,
            "notes": notes,
            "is_active": True,
        }
    )

    return JsonResponse({
        "success": True,
        "created": created,
        "schedule_id": schedule.id,
        "staff_name": staff.get_full_name() or staff.username,
        "date": sched_date.strftime("%Y-%m-%d"),
        "shift_label": str(template) if template else f"{start_time_str} – {end_time_str}",
        "duration_hours": schedule.duration_hours,
    })


@login_required
@tenant_required
@role_required("manager", "owner")
@require_POST
def delete_schedule_entry(request, schedule_id):
    """
    Soft-deletes (deactivates) a schedule entry.
    Hard-delete is not used so historical records remain intact.
    """
    try:
        schedule = StaffSchedule.objects.get(
            id=schedule_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
    except StaffSchedule.DoesNotExist:
        return JsonResponse({"error": "Schedule not found"}, status=404)

    schedule.is_active = False
    schedule.save(update_fields=["is_active"])
    return JsonResponse({"success": True})


# ==========================================================
#  SHIFT TEMPLATES (CRUD)
# ==========================================================

@login_required
@tenant_required
@role_required("manager", "owner")
def shift_template_list(request):
    """Lists all shift templates for the outlet. Managers/Owners only."""
    templates = ShiftTemplate.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    ).order_by("start_time")

    return render(request, "shifts/shift_templates.html", {"templates": templates})


@login_required
@tenant_required
@role_required("manager", "owner")
@require_POST
def create_shift_template(request):
    """
    Creates a shift template.

    Edge cases:
    - Duplicate name per outlet → reject.
    - end_time < start_time → overnight shift, allowed, validated by duration > 0.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name = data.get("name", "").strip()
    start_str = data.get("start_time")
    end_str = data.get("end_time")
    base_pay = data.get("base_pay", 0)

    if not name or not start_str or not end_str:
        return JsonResponse({"error": "name, start_time, and end_time are required"}, status=400)

    from datetime import datetime
    try:
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()
    except ValueError:
        return JsonResponse({"error": "Invalid time format (HH:MM)"}, status=400)

    from decimal import Decimal, InvalidOperation
    try:
        base_pay = Decimal(str(base_pay))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid base_pay value"}, status=400)

    if ShiftTemplate.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet, name__iexact=name
    ).exists():
        return JsonResponse({"error": f"Template '{name}' already exists"}, status=409)

    template = ShiftTemplate.objects.create(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        name=name,
        start_time=start_time,
        end_time=end_time,
        base_pay=base_pay,
    )
    return JsonResponse({
        "success": True,
        "id": template.id,
        "name": template.name,
        "label": str(template),
    })


@login_required
@tenant_required
@role_required("manager", "owner")
@require_POST
def delete_shift_template(request, template_id):
    """
    Deletes a shift template.
    If schedules are using it, schedules are set to template=NULL (SET_NULL FK).
    """
    try:
        template = ShiftTemplate.objects.get(
            id=template_id, tenant=request.user.tenant, outlet=request.user.outlet
        )
    except ShiftTemplate.DoesNotExist:
        return JsonResponse({"error": "Template not found"}, status=404)

    template.delete()
    return JsonResponse({"success": True})
