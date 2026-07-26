"""Feature flag management UI — superuser only."""
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

logger = logging.getLogger("pos.accounts")

_FEATURE_META = {
    "floor_plan":          {"label": "Floor Plan",            "icon": "bi-grid-3x3",                 "desc": "Visual table map for dine-in.",                           "group": "Ordering & Billing"},
    "token_system":        {"label": "Token Ordering",        "icon": "bi-123",                      "desc": "QSR counter tokens.",                                     "group": "Ordering & Billing"},
    "simple_billing":      {"label": "Simple Billing",        "icon": "bi-lightning-charge",         "desc": "Stripped-down billing without table selection.",          "group": "Ordering & Billing"},
    "direct_billing_mode": {"label": "Direct Billing Mode",   "icon": "bi-arrow-right-circle",       "desc": "Cashiers skip token dashboard → go straight to billing.", "group": "Ordering & Billing"},
    "split_bill":          {"label": "Split Bill",            "icon": "bi-scissors",                 "desc": "Divide a bill between guests or payment methods.",        "group": "Ordering & Billing"},
    "running_order":       {"label": "Running Order View",    "icon": "bi-list-check",               "desc": "Live sidebar of active order items.",                     "group": "Ordering & Billing"},
    "merge_tables":        {"label": "Merge Tables",          "icon": "bi-diagram-3",                "desc": "Combine tables into a single order.",                     "group": "Ordering & Billing"},
    "qr_menu":             {"label": "Digital QR Menu",       "icon": "bi-qr-code",                  "desc": "Customer-facing menu via QR scan.",                       "group": "Ordering & Billing"},
    "modifiers":           {"label": "Item Modifiers",        "icon": "bi-sliders",                  "desc": "Add-ons per menu item (e.g. no onions).",                 "group": "Ordering & Billing"},
    "platform_sync":       {"label": "Platform Sync",         "icon": "bi-arrow-left-right",         "desc": "Toggle availability on Zomato and Swiggy.",               "group": "Ordering & Billing"},
    "kot_system":          {"label": "KOT Printing",          "icon": "bi-printer",                  "desc": "Kitchen Order Tickets on order confirmation.",            "group": "Kitchen"},
    "kitchen_display":     {"label": "Kitchen Display (KDS)", "icon": "bi-display",                  "desc": "Real-time KDS screen — replaces paper tickets.",          "group": "Kitchen"},
    "multi_kitchen":       {"label": "Multi-Kitchen Stations","icon": "bi-diagram-2",                "desc": "Route items to multiple kitchen stations.",               "group": "Kitchen"},
    "waiter_call":         {"label": "Waiter Call (QR)",      "icon": "bi-bell",                     "desc": "Customers summon a waiter via QR.",                      "group": "Kitchen"},
    "inventory":           {"label": "Inventory",             "icon": "bi-boxes",                    "desc": "Real-time stock tracking with auto-deduction.",           "group": "Inventory"},
    "barcode_transfer":    {"label": "Barcode Transfer",      "icon": "bi-upc-scan",                 "desc": "Scan barcodes to receive items from central kitchen.",     "group": "Inventory"},
    "central_kitchen":     {"label": "Central Kitchen",       "icon": "bi-building-gear",            "desc": "Create production batches, dispatch to outlets, receive via barcode.", "group": "Inventory"},
    "purchase_orders":     {"label": "Purchase Orders",       "icon": "bi-cart-check",               "desc": "Raise and track POs to vendors.",                         "group": "Inventory"},
    "reports":             {"label": "Reports & Analytics",   "icon": "bi-bar-chart-line",           "desc": "Sales, item, and shift reports with CSV export.",         "group": "Reports"},
    "gstr_export":         {"label": "GSTR-1 Export",         "icon": "bi-file-earmark-spreadsheet", "desc": "Export GSTR-1 compatible CSV for GST filing.",            "group": "Reports"},
    "advanced_reports":    {"label": "Advanced Reports",      "icon": "bi-graph-up-arrow",           "desc": "Hourly trends and custom date ranges.",                   "group": "Reports"},
    "crm":                 {"label": "CRM / Guests",          "icon": "bi-people",                   "desc": "Guest profiles, visit history, and spend.",               "group": "CRM & Loyalty"},
    "reservations":        {"label": "Reservations",          "icon": "bi-calendar-check",           "desc": "Table booking with date, time, guest count.",             "group": "CRM & Loyalty"},
    "loyalty_points":      {"label": "Loyalty Points",        "icon": "bi-star",                     "desc": "Earn and redeem points per order.",                       "group": "CRM & Loyalty"},
    "guest_feedback":      {"label": "Guest Feedback",        "icon": "bi-chat-heart",               "desc": "Post-visit star rating + comment, collected via a link on the digital bill.", "group": "CRM & Loyalty"},
    "ai_menu_import":      {"label": "AI Menu Import",        "icon": "bi-stars",                    "desc": "Photograph any menu — AI imports all items in 60s.",      "group": "Menu"},
    "ai_recipe_import":    {"label": "AI Recipe Import",      "icon": "bi-journal-richtext",         "desc": "Upload a recipe — AI matches ingredients to inventory, human reviews before saving.", "group": "Menu"},
    "multi_outlet":        {"label": "Multi-Outlet",          "icon": "bi-building",                 "desc": "Multiple branch management and menu sync.",               "group": "Operations"},
    "shift_management":    {"label": "Shift Management",      "icon": "bi-clock",                    "desc": "Cash sessions, staff schedules.",                         "group": "Operations"},
    "role_based_access":   {"label": "Role-Based Access",     "icon": "bi-shield-check",             "desc": "Per-role permissions (owner / manager / cashier / waiter).", "group": "Operations"},
    "razorpay_gateway":    {"label": "Razorpay Payments",      "icon": "bi-qr-code-scan",             "desc": "UPI QR via the tenant's own Razorpay account — auto-confirms via webhook.", "group": "Payments & Notifications"},
    "whatsapp_receipts":   {"label": "WhatsApp Receipts",      "icon": "bi-whatsapp",                 "desc": "Send a bill link to the customer's WhatsApp after payment.", "group": "Payments & Notifications"},
}


@login_required
def feature_flags_view(request):
    from core.features import TENANT_FEATURES, FEATURE_GROUPS
    from tenants.models import Tenant, TenantFeatureOverride

    if not request.user.is_superuser:
        return HttpResponseForbidden("Feature management is restricted to Rasova staff.")

    all_tenants = Tenant.objects.order_by("name")
    tenant_id   = request.GET.get("tenant_id")

    if not tenant_id:
        return render(request, "accounts/feature_flags.html", {
            "tenant": None, "all_tenants": all_tenants,
        })

    try:
        tenant = Tenant.objects.get(id=tenant_id)
    except Tenant.DoesNotExist:
        return HttpResponseForbidden("Tenant not found.")

    overrides       = {o.feature: o for o in TenantFeatureOverride.objects.filter(tenant=tenant)}
    default_features = set(TENANT_FEATURES.get(tenant.tenant_type, TENANT_FEATURES["fine_dining"]))

    grouped_features = {}
    for group_name, feature_keys in FEATURE_GROUPS.items():
        group_items = []
        for key in feature_keys:
            meta = _FEATURE_META.get(key)
            if not meta:
                continue
            override = overrides.get(key)
            if override is not None:
                state, source = ("on" if override.enabled else "off"), "override"
            elif key in default_features:
                state, source = "on", "default"
            else:
                state, source = "off", "default"
            group_items.append({
                "key": key, "label": meta["label"], "icon": meta["icon"],
                "desc": meta["desc"], "state": state, "source": source,
            })
        if group_items:
            grouped_features[group_name] = group_items

    return render(request, "accounts/feature_flags.html", {
        "grouped_features":    grouped_features,
        "tenant":              tenant,
        "tenant_type_display": tenant.tenant_type.replace("_", " ").title(),
        "all_tenants":         all_tenants,
    })


@login_required
@require_POST
def toggle_feature_flag(request):
    from tenants.models import Tenant, TenantFeatureOverride, TenantFeatureAuditLog
    from core.features import TENANT_FEATURES

    if not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    tenant_id = data.get("tenant_id")
    feature   = data.get("feature", "").strip()
    enabled   = data.get("enabled")

    if not tenant_id:
        return JsonResponse({"error": "tenant_id required"}, status=400)
    if not feature or feature not in _FEATURE_META:
        return JsonResponse({"error": f"Unknown feature: {feature!r}"}, status=400)
    if enabled is None:
        return JsonResponse({"error": "enabled field required"}, status=400)

    try:
        tenant = Tenant.objects.get(id=tenant_id)
    except Tenant.DoesNotExist:
        return JsonResponse({"error": "Tenant not found"}, status=404)

    default_features = set(TENANT_FEATURES.get(tenant.tenant_type, TENANT_FEATURES["fine_dining"]))
    if bool(enabled) == (feature in default_features):
        TenantFeatureOverride.objects.filter(tenant=tenant, feature=feature).delete()
        source = "default"
    else:
        TenantFeatureOverride.objects.update_or_create(
            tenant=tenant, feature=feature,
            defaults={"enabled": enabled, "notes": f"Set by {request.user.username}"},
        )
        source = "override"

    # Append-only history — TenantFeatureOverride above is a single mutable row
    # per (tenant, feature), so without this, past changes are lost on the next toggle.
    TenantFeatureAuditLog.objects.create(
        tenant=tenant, feature=feature, enabled=bool(enabled), source=source,
        changed_by=request.user, notes=f"Set by {request.user.username}",
    )

    if hasattr(tenant, "_feature_overrides"):
        del tenant._feature_overrides

    return JsonResponse({"success": True, "feature": feature, "enabled": bool(enabled), "source": source})
