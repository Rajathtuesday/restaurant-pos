"""
Rasova Portal — /portal/
Internal operations panel for Rasova staff (is_superuser=True).
"""
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import User
from tenants.models import Tenant, Outlet, TenantFeatureOverride
from setup.models import KitchenStation, PaymentConfig

logger = logging.getLogger("pos.portal")

PRESETS = {
    "qsr_no_kds": {
        "label": "QSR — counter, no kitchen screen",
        "icon":  "bi-ticket-perforated",
        "color": "#10b981",
        "enable":  ["token_system", "kot_system", "inventory", "reports",
                    "ai_menu_import", "direct_billing_mode",
                    "parcel_charge", "composition_scheme"],
        "disable": ["kitchen_display", "floor_plan", "waiter_call",
                    "split_bill", "merge_tables", "crm", "reservations",
                    "counter_billing"],
        "outlet":  {"split_bill_by_category": False},
    },
    "counter_billing": {
        "label": "Counter Billing — multi-section hotel / food court",
        "icon":  "bi-receipt-cutoff",
        "color": "#6366f1",
        "enable":  ["token_system", "kot_system", "inventory", "reports",
                    "ai_menu_import", "counter_billing",
                    "parcel_charge", "composition_scheme"],
        "disable": ["kitchen_display", "floor_plan", "waiter_call",
                    "split_bill", "merge_tables", "crm", "reservations"],
        "outlet":  {"split_bill_by_category": True},
    },
    "fine_dining": {
        "label": "Fine Dining — full table service",
        "icon":  "bi-table",
        "color": "#c5a059",
        "enable":  ["floor_plan", "waiter_call", "kitchen_display", "kot_system",
                    "merge_tables", "split_bill", "qr_menu", "running_order",
                    "inventory", "reports", "ai_menu_import", "crm",
                    "composition_scheme", "parcel_charge"],
        "disable": ["token_system", "simple_billing", "direct_billing_mode",
                    "barcode_transfer", "counter_billing"],
        "outlet":  {"split_bill_by_category": False},
    },
    "cafe": {
        "label": "Café — mixed counter + tables",
        "icon":  "bi-cup-hot",
        "color": "#f59e0b",
        "enable":  ["token_system", "floor_plan", "qr_menu", "waiter_call",
                    "kot_system", "kitchen_display", "inventory", "reports",
                    "ai_menu_import", "parcel_charge", "composition_scheme"],
        "disable": ["merge_tables", "split_bill", "crm", "reservations",
                    "barcode_transfer", "counter_billing"],
        "outlet":  {"split_bill_by_category": False},
    },
}


def _su_only(request):
    if not request.user.is_authenticated or not request.user.is_superuser:
        return HttpResponseForbidden("Superuser access only.")
    return None


@login_required
def portal_home(request):
    if (denied := _su_only(request)):
        return denied
    tenants = Tenant.objects.prefetch_related("outlets").order_by("-created_at")
    tenant_data = []
    for t in tenants:
        outlets    = list(t.outlets.all())
        outlet     = outlets[0] if outlets else None
        user_count = User.objects.filter(tenant=t).count()
        tenant_data.append({"tenant": t, "outlet": outlet, "user_count": user_count})
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
    except Exception as e:
        logger.exception("Error creating restaurant")
        return JsonResponse({"error": str(e)}, status=500)


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

    from core.features import TENANT_FEATURES
    overrides        = {o.feature: o.enabled for o in TenantFeatureOverride.objects.filter(tenant=tenant)}
    default_features = set(TENANT_FEATURES.get(tenant.tenant_type, []))
    feat_on = lambda k: overrides[k] if k in overrides else k in default_features

    key_features = [
        "token_system", "floor_plan", "kitchen_display", "kot_system",
        "inventory", "waiter_call", "qr_menu", "direct_billing_mode",
        "counter_billing", "composition_scheme", "parcel_charge",
    ]
    feature_summary = [{"key": k, "on": feat_on(k)} for k in key_features]

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_outlet":
            outlet.phone        = request.POST.get("phone", "").strip() or None
            outlet.gst_no       = request.POST.get("gst_no", "").strip().upper() or None
            outlet.fssai_no     = request.POST.get("fssai_no", "").strip() or None
            outlet.address      = request.POST.get("address", "").strip()
            outlet.sac_code     = request.POST.get("sac_code", "996331").strip() or "996331"
            outlet.gst_inclusive           = request.POST.get("gst_inclusive") == "true"
            outlet.is_composition_scheme   = "is_composition_scheme"   in request.POST
            outlet.split_bill_by_category  = "split_bill_by_category"  in request.POST
            try:
                outlet.parcel_charge_amount = Decimal(request.POST.get("parcel_charge_amount", "0") or "0")
            except Exception:
                pass
            outlet.save()
            return redirect(f"/portal/tenant/{tenant_id}/")

        if action == "update_printer":
            station = get_object_or_404(KitchenStation, id=request.POST.get("station_id"), tenant=tenant)
            station.printer_ip     = request.POST.get("printer_ip", "").strip() or None
            station.printer_port   = int(request.POST.get("printer_port") or 9100)
            station.paper_width_mm = int(request.POST.get("paper_width_mm") or 80)
            station.cut_type       = request.POST.get("cut_type", "partial")
            station.save()
            return redirect(f"/portal/tenant/{tenant_id}/")

        if action == "add_station":
            sname = request.POST.get("station_name", "").strip()
            if sname:
                KitchenStation.objects.create(tenant=tenant, outlet=outlet, name=sname, is_default=False)
            return redirect(f"/portal/tenant/{tenant_id}/")

        if action == "update_payment":
            config.cash_enabled = "cash" in request.POST.getlist("methods")
            config.upi_enabled  = "upi"  in request.POST.getlist("methods")
            config.card_enabled = "card" in request.POST.getlist("methods")
            config.upi_id       = request.POST.get("upi_id", "").strip().lower()
            config.save()
            return redirect(f"/portal/tenant/{tenant_id}/")

        if action == "add_staff":
            uname = request.POST.get("username", "").strip()
            role  = request.POST.get("role", "cashier")
            pwd   = request.POST.get("password", "").strip()
            if uname and pwd and not User.objects.filter(username=uname).exists():
                User.objects.create_user(username=uname, password=pwd, tenant=tenant, outlet=outlet, role=role)
            return redirect(f"/portal/tenant/{tenant_id}/")

    return render(request, "portal/tenant.html", {
        "tenant": tenant, "outlet": outlet, "stations": stations,
        "staff": staff, "config": config,
        "feature_summary": feature_summary, "presets": PRESETS,
    })


@login_required
@require_POST
def apply_preset(request, tenant_id):
    if (denied := _su_only(request)):
        return denied
    tenant     = get_object_or_404(Tenant, id=tenant_id)
    preset_key = request.POST.get("preset")
    preset     = PRESETS.get(preset_key)
    if not preset:
        return JsonResponse({"error": "Unknown preset"}, status=400)
    with transaction.atomic():
        for f in preset.get("enable", []):
            TenantFeatureOverride.objects.update_or_create(tenant=tenant, feature=f, defaults={"enabled": True})
        for f in preset.get("disable", []):
            TenantFeatureOverride.objects.update_or_create(tenant=tenant, feature=f, defaults={"enabled": False})
        outlet = tenant.outlets.first()
        if outlet and preset.get("outlet"):
            for field, val in preset["outlet"].items():
                setattr(outlet, field, val)
            outlet.save()
    logger.info("Portal %s applied preset '%s' to '%s'", request.user.username, preset_key, tenant.name)
    return JsonResponse({"success": True, "applied": preset_key, "label": preset["label"]})
