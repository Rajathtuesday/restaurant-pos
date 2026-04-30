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
    """
    Renders the Cash Sessions dashboard (Cash Register Management).
    Shows the active session and historical sessions for the current outlet.
    Managers/Owners see all sessions; Cashiers/Staff see only their active sessions.
    """
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

        expected_cash = Decimal(str(session.opening_balance)) + Decimal(str(cash_payments or 0))

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

    today = timezone.localdate()
    sessions = CashSession.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        opened_at__date=today
    ).order_by('opened_at')

    # Calculate Daily Summary
    payments = Payment.objects.filter(
        order__tenant=request.user.tenant,
        order__outlet=request.user.outlet,
        paid_at__date=today
    )
    
    summary = payments.aggregate(
        total=Sum("amount"),
        cash=Sum("amount", filter=Q(method="cash")),
        digital=Sum("amount", filter=Q(method__in=["upi", "card"])),
        refunds=Sum("amount", filter=Q(method="refund"))
    )

    orders = Order.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        created_at__date=today,
        status__in=["paid", "closed"]
    ).aggregate(
        subtotal=Sum("subtotal"),
        discount=Sum("discount_total"),
        gst=Sum("gst_total"),
        round_off=Sum("round_off")
    )

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="z_report_{today}.csv"'

    writer = csv.writer(response)
    
    writer.writerow(['DAILY Z-REPORT SUMMARY', today.strftime('%d %b %Y')])
    writer.writerow(['Outlet', request.user.outlet.name])
    writer.writerow([])
    
    writer.writerow(['FINANCIAL TOTALS'])
    writer.writerow(['Gross Subtotal', orders['subtotal'] or 0])
    writer.writerow(['Total Discount', orders['discount'] or 0])
    writer.writerow(['GST Collected', orders['gst'] or 0])
    writer.writerow(['Round Off', orders['round_off'] or 0])
    writer.writerow(['Net Refunds', abs(summary['refunds'] or 0)])
    writer.writerow(['NET REVENUE', summary['total'] or 0])
    writer.writerow([])

    writer.writerow(['PAYMENT BREAKDOWN'])
    writer.writerow(['Cash', summary['cash'] or 0])
    writer.writerow(['Digital (UPI/Card)', summary['digital'] or 0])
    writer.writerow([])

    # Detailed Item Sales for the Day
    from django.db.models import Count, F, ExpressionWrapper, DecimalField
    from orders.models import OrderItem
    
    item_sales = OrderItem.objects.filter(
        order__tenant=request.user.tenant,
        order__outlet=request.user.outlet,
        order__created_at__date=today,
        order__status__in=["paid", "closed"]
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
    writer.writerow(['Session ID', 'Status', 'Opened At', 'Closed At', 'Opened By', 'Opening Bal', 'Exp Cash', 'Actual Cash', 'Diff', 'Sales'])

    for s in sessions:
        writer.writerow([
            s.id,
            s.status.upper(),
            s.opened_at.strftime('%H:%M') if s.opened_at else '',
            s.closed_at.strftime('%H:%M') if s.closed_at else '-',
            s.opened_by.username if s.opened_by else '',
            s.opening_balance,
            s.expected_cash,
            s.actual_cash,
            s.discrepancy,
            s.total_sales
        ])

    return response
