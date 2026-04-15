# shifts/views.py
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib import messages

from core.decorators import tenant_required
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
    tenant = request.user.tenant
    outlet = request.user.outlet

    shift = Shift.objects.filter(
        tenant=tenant, outlet=outlet,
        staff=request.user, clocked_out_at__isnull=True
    ).first()

    if not shift:
        return JsonResponse({"error": "No active shift found"}, status=400)

    data = {}
    try:
        data = json.loads(request.body)
    except Exception:
        pass

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
@require_POST
def update_shift_tips(request, shift_id):
    """Manager can update tips for any shift."""
    if request.user.role not in ("manager", "owner") and not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
        shift = Shift.objects.get(id=shift_id, tenant=request.user.tenant)
        shift.tips = data.get("tips", 0)
        shift.save(update_fields=["tips"])
        return JsonResponse({"success": True})
    except Shift.DoesNotExist:
        return JsonResponse({"error": "Shift not found"}, status=404)


@login_required
@tenant_required
def cash_session_list(request):
    """List of all EOD cash sessions."""
    if request.user.role not in ("manager", "owner") and not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

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
    """Start a new cash register session."""
    from .models import CashSession
    try:
        data = json.loads(request.body)
        opening_balance = float(data.get("opening_balance", 0))

        # Check for already open session
        existing = CashSession.objects.filter(
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
    from django.db.models import Sum

    try:
        data = json.loads(request.body)
        actual_cash = float(data.get("actual_cash", 0))

        session = CashSession.objects.filter(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            status="open"
        ).first()

        if not session:
            return JsonResponse({"error": "No open session found"}, status=400)

        # 1. Calculate Expected Cash (Opening + All Cash Payments since opened_at)
        cash_payments = Payment.objects.filter(
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet,
            method="cash",
            paid_at__gte=session.opened_at
        ).aggregate(total=Sum("amount"))["total"] or 0

        expected_cash = float(session.opening_balance) + float(cash_payments)

        # 2. Calculate Digital Payments
        digital_payments = Payment.objects.filter(
            order__tenant=request.user.tenant,
            order__outlet=request.user.outlet,
            method__in=["upi", "card"],
            paid_at__gte=session.opened_at
        ).aggregate(total=Sum("amount"))["total"] or 0

        # 3. Total Sales (Grand total of orders paid in this window)
        total_sales = float(cash_payments) + float(digital_payments)

        session.closed_at = timezone.now()
        session.closed_by = request.user
        session.expected_cash = expected_cash
        session.actual_cash = actual_cash
        session.discrepancy = float(actual_cash) - float(expected_cash)
        session.total_digital_payments = digital_payments
        session.total_sales = total_sales
        session.status = "closed"
        session.save()

        return JsonResponse({"success": True, "discrepancy": float(session.discrepancy)})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
