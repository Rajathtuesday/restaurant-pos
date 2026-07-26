# setup/views/report_subscription_views.py
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from core.decorators import tenant_required
from tenants.models import Outlet
from ..models import ScheduledReportSubscription


@login_required
@tenant_required
def report_subscriptions(request):
    """Owner-only settings page: who gets the daily report-digest email."""
    if request.user.role != "owner":
        return HttpResponseForbidden()

    subscriptions = ScheduledReportSubscription.objects.filter(
        tenant=request.user.tenant
    ).select_related("outlet").order_by("-created_at")
    outlets = Outlet.objects.filter(tenant=request.user.tenant)

    return render(request, "setup/report_subscriptions.html", {
        "subscriptions": subscriptions,
        "outlets": outlets,
    })


@login_required
@tenant_required
@require_POST
def report_subscription_create(request):
    if request.user.role != "owner":
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)

    raw_emails = (data.get("recipient_emails") or "").strip()
    if not raw_emails:
        return JsonResponse({"error": "At least one recipient email is required."}, status=400)

    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    emails = [e.strip() for e in raw_emails.split(",") if e.strip()]
    for email in emails:
        try:
            validate_email(email)
        except ValidationError:
            return JsonResponse({"error": f"'{email}' is not a valid email address."}, status=400)

    outlet = None
    outlet_id = data.get("outlet_id")
    if outlet_id:
        try:
            outlet = Outlet.objects.get(id=outlet_id, tenant=request.user.tenant)
        except Outlet.DoesNotExist:
            return JsonResponse({"error": "Invalid outlet."}, status=400)

    sub = ScheduledReportSubscription.objects.create(
        tenant=request.user.tenant, outlet=outlet,
        recipient_emails=", ".join(emails), created_by=request.user,
    )
    return JsonResponse({"success": True, "id": sub.id})


@login_required
@tenant_required
@require_POST
def report_subscription_toggle(request, subscription_id):
    if request.user.role != "owner":
        return JsonResponse({"error": "Permission denied"}, status=403)

    sub = get_object_or_404(
        ScheduledReportSubscription, id=subscription_id, tenant=request.user.tenant
    )
    sub.is_active = not sub.is_active
    sub.save(update_fields=["is_active"])
    return JsonResponse({"success": True, "is_active": sub.is_active})


@login_required
@tenant_required
@require_POST
def report_subscription_delete(request, subscription_id):
    if request.user.role != "owner":
        return JsonResponse({"error": "Permission denied"}, status=403)

    sub = get_object_or_404(
        ScheduledReportSubscription, id=subscription_id, tenant=request.user.tenant
    )
    sub.delete()
    return JsonResponse({"success": True})
