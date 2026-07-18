"""
Rasova Portal — /portal/
Internal operations panel for Rasova staff (is_superuser=True).
"""
import logging

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User
from tenants.models import Tenant, Outlet
from setup.models import KitchenStation, PaymentConfig
from tenants.services import tenant_config_service as tcs

logger = logging.getLogger("pos.portal")

# Canonical preset library now lives in tenants/services/tenant_config_service.py,
# shared with accounts/views/superuser_views.py — aliased here so every
# existing reference in this file keeps working unchanged.
PRESETS = tcs.PRESETS


def _su_only(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return HttpResponseForbidden("Superuser access only.")
    return None


@login_required
def portal_home(request):
    if (denied := _su_only(request)):
        return denied
    tenants = Tenant.objects.prefetch_related("outlets").order_by("-created_at")
    # One query for every tenant's user count instead of one query PER
    # tenant in the loop below.
    user_counts = {
        row["tenant_id"]: row["c"]
        for row in User.objects.values("tenant_id").annotate(c=Count("id"))
    }
    tenant_data = []
    for t in tenants:
        outlets    = list(t.outlets.all())
        outlet     = outlets[0] if outlets else None
        tenant_data.append({"tenant": t, "outlet": outlet, "user_count": user_counts.get(t.id, 0)})
    return render(request, "portal/home.html", {"tenant_data": tenant_data, "presets": PRESETS})


@login_required
@require_POST
def create_restaurant(request):
    if (denied := _su_only(request)):
        return denied
    name           = request.POST.get("name", "").strip()
    tenant_type    = request.POST.get("tenant_type", "cafe")
    outlet_name    = request.POST.get("outlet_name", "").strip() or "Main Counter"
    phone          = request.POST.get("phone", "").strip()
    gst_no         = request.POST.get("gst_no", "").strip().upper()
    owner_username = request.POST.get("owner_username", "").strip()
    owner_password = request.POST.get("owner_password", "").strip()
    preset_key     = request.POST.get("preset", "")
    if not name or not owner_username or not owner_password:
        return JsonResponse({"error": "Name, username and password required."}, status=400)
    if User.objects.filter(username=owner_username).exists():
        return JsonResponse({"error": f"Username '{owner_username}' already taken."}, status=400)
    try:
        with transaction.atomic():
            tenant = Tenant.objects.create(name=name, tenant_type=tenant_type)
            outlet = Outlet.objects.create(
                tenant=tenant, name=outlet_name,
                phone=phone or None, gst_no=gst_no or None,
            )
            if preset_key and preset_key in PRESETS:
                for field, val in PRESETS[preset_key].get("outlet", {}).items():
                    setattr(outlet, field, val)
                outlet.save()
            User.objects.create_user(
                username=owner_username, password=owner_password,
                tenant=tenant, outlet=outlet, role="owner",
            )
            KitchenStation.objects.create(tenant=tenant, outlet=outlet, name="Counter", is_default=True)
            PaymentConfig.for_outlet(outlet, tenant)
        logger.info("Portal %s created '%s' (%s)", request.user.username, name, tenant_type)
        return JsonResponse({"success": True, "tenant_id": tenant.id})
    except Exception:
        logger.exception("Error creating restaurant")
        return JsonResponse({"error": "Restaurant could not be created. Please try again."}, status=500)


@login_required
def tenant_config(request, tenant_id):
    if (denied := _su_only(request)):
        return denied
    tenant  = get_object_or_404(Tenant, id=tenant_id)
    outlet  = tenant.outlets.first()
    if not outlet:
        return render(request, "portal/home.html", {"error": f"Tenant '{tenant.name}' has no outlet."})

    stations  = KitchenStation.objects.filter(tenant=tenant, outlet=outlet)
    staff     = User.objects.filter(tenant=tenant, outlet=outlet).order_by("role", "username")
    config, _ = PaymentConfig.for_outlet(outlet, tenant)
    feature_summary = tcs.get_feature_summary(tenant)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_outlet":
            tcs.update_outlet_from_post(outlet, request.POST)
            return redirect(f"/portal/tenant/{tenant_id}/")

        if action == "update_printer":
            tcs.update_printer_from_post(tenant, request.POST)
            return redirect(f"/portal/tenant/{tenant_id}/")

        if action == "add_station":
            tcs.add_station_from_post(tenant, outlet, request.POST)
            return redirect(f"/portal/tenant/{tenant_id}/")

        if action == "update_payment":
            tcs.update_payment_from_post(config, request.POST)
            return redirect(f"/portal/tenant/{tenant_id}/")

        if action == "add_staff":
            tcs.add_staff_from_post(tenant, outlet, request.POST)
            return redirect(f"/portal/tenant/{tenant_id}/")

    return render(request, "portal/tenant.html", {
        "tenant": tenant, "outlet": outlet, "stations": stations,
        "staff": staff, "config": config,
        "feature_summary": feature_summary, "presets": tcs.PRESETS,
    })


@login_required
@require_POST
def apply_preset(request, tenant_id):
    if (denied := _su_only(request)):
        return denied
    tenant     = get_object_or_404(Tenant, id=tenant_id)
    preset_key = request.POST.get("preset")
    with transaction.atomic():
        preset = tcs.apply_preset_to_tenant(tenant, preset_key, request.user)
    if not preset:
        return JsonResponse({"error": "Unknown preset"}, status=400)
    logger.info("Portal %s applied preset '%s' to '%s'", request.user.username, preset_key, tenant.name)
    return JsonResponse({"success": True, "applied": preset_key, "label": preset["label"]})
