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
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User
from tenants.models import Tenant, Outlet, TenantFeatureOverride
from setup.models import KitchenStation, PaymentConfig

logger = logging.getLogger("pos.superuser")

# Feature presets — what each restaurant type gets enabled/disabled
PRESETS = {
    "qsr_no_kds": {
        "label": "QSR — no kitchen screen (print strip)",
        "enable":  ["token_system", "kot_system", "inventory", "reports",
                    "ai_menu_import", "direct_billing_mode"],
        "disable": ["kitchen_display", "floor_plan", "waiter_call",
                    "split_bill", "merge_tables", "crm", "reservations"],
    },
    "qsr_kds": {
        "label": "QSR — with kitchen display",
        "enable":  ["token_system", "kot_system", "kitchen_display",
                    "inventory", "reports", "ai_menu_import", "direct_billing_mode"],
        "disable": ["floor_plan", "waiter_call", "split_bill",
                    "merge_tables", "crm", "reservations"],
    },
    "fine_dining": {
        "label": "Fine Dining — full table service",
        "enable":  ["floor_plan", "waiter_call", "kitchen_display", "kot_system",
                    "merge_tables", "split_bill", "qr_menu", "running_order",
                    "inventory", "reports", "ai_menu_import", "crm"],
        "disable": ["token_system", "simple_billing", "direct_billing_mode",
                    "barcode_transfer"],
    },
    "cafe": {
        "label": "Café — mixed counter + tables",
        "enable":  ["token_system", "floor_plan", "qr_menu", "waiter_call",
                    "kot_system", "kitchen_display", "inventory", "reports",
                    "ai_menu_import"],
        "disable": ["merge_tables", "split_bill", "crm", "reservations",
                    "barcode_transfer"],
    },
}


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

    # Annotate each tenant with outlet + user count for display
    tenant_data = []
    for t in tenants:
        outlets = list(t.outlets.all())
        outlet  = outlets[0] if outlets else None
        user_count = User.objects.filter(tenant=t).count()
        tenant_data.append({
            "tenant":     t,
            "outlet":     outlet,
            "user_count": user_count,
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
            PaymentConfig.objects.get_or_create(tenant=tenant, outlet=outlet)

        logger.info(
            "Superuser %s created restaurant '%s' (%s) with owner '%s'",
            request.user.username, name, tenant_type, owner_username,
        )
        return JsonResponse({"success": True, "tenant_id": tenant.id})

    except Exception as e:
        logger.exception("Error creating restaurant")
        return JsonResponse({"error": str(e)}, status=500)


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
    config, _ = PaymentConfig.objects.get_or_create(tenant=tenant, outlet=outlet)

    # Current feature state
    from core.features import TENANT_FEATURES
    overrides       = {o.feature: o.enabled for o in TenantFeatureOverride.objects.filter(tenant=tenant)}
    default_features = set(TENANT_FEATURES.get(tenant.tenant_type, []))

    def feat_on(key):
        if key in overrides:
            return overrides[key]
        return key in default_features

    key_features = [
        "token_system", "floor_plan", "kitchen_display", "kot_system",
        "inventory", "waiter_call", "qr_menu", "direct_billing_mode",
    ]
    feature_summary = [
        {"key": k, "on": feat_on(k)} for k in key_features
    ]

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_printer":
            station_id = request.POST.get("station_id")
            station    = get_object_or_404(KitchenStation, id=station_id, tenant=tenant)
            station.printer_ip   = request.POST.get("printer_ip", "").strip() or None
            station.printer_port = int(request.POST.get("printer_port") or 9100)
            station.paper_width_mm = int(request.POST.get("paper_width_mm") or 80)
            station.cut_type     = request.POST.get("cut_type", "partial")
            station.save()
            logger.info("SU %s updated printer for station %s (tenant %s)", request.user.username, station.name, tenant.name)
            return redirect(f"/superuser/tenant/{tenant_id}/")

        if action == "add_station":
            sname = request.POST.get("station_name", "").strip()
            if sname:
                KitchenStation.objects.create(
                    tenant=tenant, outlet=outlet, name=sname, is_default=False,
                )
            return redirect(f"/superuser/tenant/{tenant_id}/")

        if action == "update_payment":
            config.cash_enabled = "cash" in request.POST.getlist("methods")
            config.upi_enabled  = "upi"  in request.POST.getlist("methods")
            config.card_enabled = "card" in request.POST.getlist("methods")
            config.upi_id       = request.POST.get("upi_id", "").strip().lower()
            config.save()
            return redirect(f"/superuser/tenant/{tenant_id}/")

        if action == "add_staff":
            uname = request.POST.get("username", "").strip()
            role  = request.POST.get("role", "cashier")
            pwd   = request.POST.get("password", "").strip()
            if uname and pwd and not User.objects.filter(username=uname).exists():
                User.objects.create_user(
                    username=uname, password=pwd,
                    tenant=tenant, outlet=outlet, role=role,
                )
            return redirect(f"/superuser/tenant/{tenant_id}/")

        if action == "update_outlet":
            outlet.phone        = request.POST.get("phone",    "").strip() or None
            outlet.gst_no       = request.POST.get("gst_no",   "").strip().upper() or None
            outlet.fssai_no     = request.POST.get("fssai_no", "").strip() or None
            outlet.address      = request.POST.get("address",  "").strip()
            outlet.sac_code     = request.POST.get("sac_code", "996331").strip() or "996331"
            outlet.gst_inclusive = request.POST.get("gst_inclusive") == "true"
            outlet.save()
            return redirect(f"/superuser/tenant/{tenant_id}/")

    return render(request, "accounts/superuser_tenant.html", {
        "tenant":          tenant,
        "outlet":          outlet,
        "stations":        stations,
        "staff":           staff,
        "config":          config,
        "feature_summary": feature_summary,
        "presets":         PRESETS,
    })


# ---------------------------------------------------------------------------
# APPLY FEATURE PRESET
# ---------------------------------------------------------------------------

@login_required
@require_POST
def apply_preset(request, tenant_id):
    if (denied := _su_only(request)):
        return denied

    tenant    = get_object_or_404(Tenant, id=tenant_id)
    preset_key = request.POST.get("preset")
    preset    = PRESETS.get(preset_key)

    if not preset:
        return JsonResponse({"error": "Unknown preset"}, status=400)

    with transaction.atomic():
        for feature in preset.get("enable", []):
            TenantFeatureOverride.objects.update_or_create(
                tenant=tenant, feature=feature,
                defaults={"enabled": True},
            )
        for feature in preset.get("disable", []):
            TenantFeatureOverride.objects.update_or_create(
                tenant=tenant, feature=feature,
                defaults={"enabled": False},
            )

    logger.info("SU %s applied preset '%s' to tenant '%s'", request.user.username, preset_key, tenant.name)
    return JsonResponse({"success": True, "applied": preset_key, "label": preset["label"]})
