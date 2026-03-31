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

    return JsonResponse({
        "success": True,
        "duration_hours": shift.duration_hours,
        "clocked_out_at": shift.clocked_out_at.isoformat()
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
