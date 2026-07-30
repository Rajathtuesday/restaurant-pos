"""
Superuser Control Panel — /superuser/

Lets Rasova staff (is_superuser=True) set up any restaurant without
logging in as that restaurant's owner. Every action here is scoped to
the target tenant/outlet, completely separate from the superuser's own
account context.
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

logger = logging.getLogger("pos.superuser")

# Canonical preset library now lives in tenants/services/tenant_config_service.py,
# shared with portal/views.py — this used to be its own independent, less
# complete copy (missing the "counter_billing" preset entirely, and every
# shared preset was missing parcel_charge/composition_scheme/outlet
# overrides that the portal copy had), so applying the "same" preset from
# the two admin panels didn't actually produce the same result. Aliased
# here so every existing reference in this file keeps working unchanged.
PRESETS = tcs.PRESETS


def _su_only(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return HttpResponseForbidden("Superuser access only.")
    return None


# ---------------------------------------------------------------------------
# MAIN PANEL
# ---------------------------------------------------------------------------

@login_required
def superuser_panel(request):
    if (denied := _su_only(request)):
        return denied

    tenants = (
        Tenant.objects
        .prefetch_related("outlets")
        .order_by("-created_at")
    )

    # One query for every tenant's user count instead of one query PER
    # tenant in the loop below.
    user_counts = {
        row["tenant_id"]: row["c"]
        for row in User.objects.values("tenant_id").annotate(c=Count("id"))
    }

    # Annotate each tenant with outlet + user count for display
    tenant_data = []
    for t in tenants:
        outlets = list(t.outlets.all())
        outlet  = outlets[0] if outlets else None
        tenant_data.append({
            "tenant":     t,
            "outlet":     outlet,
            "user_count": user_counts.get(t.id, 0),
        })

    return render(request, "accounts/superuser_panel.html", {
        "tenant_data": tenant_data,
        "presets":     PRESETS,
    })


# ---------------------------------------------------------------------------
# CREATE RESTAURANT
# ---------------------------------------------------------------------------

@login_required
@require_POST
def create_restaurant(request):
    if (denied := _su_only(request)):
        return denied

    name          = request.POST.get("name", "").strip()
    tenant_type   = request.POST.get("tenant_type", "franchise")
    outlet_name   = request.POST.get("outlet_name", "").strip() or "Main Branch"
    phone         = request.POST.get("phone", "").strip()
    gst_no        = request.POST.get("gst_no", "").strip().upper()
    owner_username = request.POST.get("owner_username", "").strip()
    owner_password = request.POST.get("owner_password", "").strip()

    if not name or not owner_username or not owner_password:
        return JsonResponse({"error": "Restaurant name, owner username and password are required."}, status=400)

    if User.objects.filter(username=owner_username).exists():
        return JsonResponse({"error": f"Username '{owner_username}' is already taken."}, status=400)

    try:
        with transaction.atomic():
            tenant = Tenant.objects.create(name=name, tenant_type=tenant_type)

            outlet = Outlet.objects.create(
                tenant=tenant,
                name=outlet_name,
                phone=phone or None,
                gst_no=gst_no or None,
            )

            User.objects.create_user(
                username=owner_username,
                password=owner_password,
                tenant=tenant,
                outlet=outlet,
                role="owner",
            )

            # Create a default kitchen station (billing/single printer)
            KitchenStation.objects.create(
                tenant=tenant, outlet=outlet,
                name="Counter", is_default=True,
            )

            # Create default payment config
            PaymentConfig.for_outlet(outlet, tenant)

        logger.info(
            "Superuser %s created restaurant '%s' (%s) with owner '%s'",
            request.user.username, name, tenant_type, owner_username,
        )
        return JsonResponse({"success": True, "tenant_id": tenant.id})

    except Exception:
        logger.exception("Error creating restaurant")
        return JsonResponse({"error": "Restaurant could not be created. Please try again."}, status=500)


# ---------------------------------------------------------------------------
# TENANT CONFIGURATION PAGE
# ---------------------------------------------------------------------------

@login_required
def tenant_config(request, tenant_id):
    if (denied := _su_only(request)):
        return denied

    tenant = get_object_or_404(Tenant, id=tenant_id)
    outlets = tenant.outlets.all()
    outlet  = outlets.first()

    if not outlet:
        return render(request, "accounts/superuser_panel.html", {
            "error": f"Tenant '{tenant.name}' has no outlet yet. Create one in Admin.",
        })

    stations = KitchenStation.objects.filter(tenant=tenant, outlet=outlet)
    staff    = User.objects.filter(tenant=tenant, outlet=outlet).order_by("role", "username")
    config, _ = PaymentConfig.for_outlet(outlet, tenant)
    feature_summary = tcs.get_feature_summary(tenant)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_printer":
            station = tcs.update_printer_from_post(tenant, request.POST)
            logger.info("SU %s updated printer for station %s (tenant %s)", request.user.username, station.name, tenant.name)
            return redirect("superuser_tenant", tenant_id=tenant_id)

        if action == "add_station":
            tcs.add_station_from_post(tenant, outlet, request.POST)
            return redirect("superuser_tenant", tenant_id=tenant_id)

        if action == "update_payment":
            tcs.update_payment_from_post(config, request.POST)
            return redirect("superuser_tenant", tenant_id=tenant_id)

        if action == "add_staff":
            tcs.add_staff_from_post(tenant, outlet, request.POST)
            return redirect("superuser_tenant", tenant_id=tenant_id)

        if action == "update_outlet":
            tcs.update_outlet_from_post(outlet, request.POST)
            return redirect("superuser_tenant", tenant_id=tenant_id)

    return render(request, "accounts/superuser_tenant.html", {
        "tenant":          tenant,
        "outlet":          outlet,
        "stations":        stations,
        "staff":           staff,
        "config":          config,
        "feature_summary": feature_summary,
        "presets":         tcs.PRESETS,
    })


# ---------------------------------------------------------------------------
# APPLY FEATURE PRESET
# ---------------------------------------------------------------------------

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

    logger.info("SU %s applied preset '%s' to tenant '%s'", request.user.username, preset_key, tenant.name)
    return JsonResponse({"success": True, "applied": preset_key, "label": preset["label"]})
