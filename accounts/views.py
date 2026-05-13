# accounts/views.py
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.http import require_POST

from core.decorators import tenant_required
from notifications.models import Notification
from reports.services.dashboard_metrics import owner_dashboard_metrics

def login_view(request):
    if request.method == "POST":

        username = request.POST.get("username", "")
        password = request.POST.get("password", "")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)

            # ROLE BASED REDIRECT
            tenant_type = user.tenant.tenant_type if user.tenant else 'fine_dining'

            if user.role in ["owner", "manager"]:
                return redirect("/dashboard/")

            elif user.role == "agent":
                return redirect("/sales/")

            elif user.role == "waiter":
                if tenant_type != 'fine_dining':
                    return redirect("token-dashboard")
                return redirect("/tables/")

            elif user.role == "chef":
                return redirect("/kitchen/")

            elif user.role == "cashier":
                if tenant_type != 'fine_dining':
                    from core.features import has_feature
                    if has_feature(user.tenant, "direct_billing_mode"):
                        return redirect("/dashboard/")
                    return redirect("token-dashboard")
                return redirect("/billing/")

            else:
                if tenant_type != 'fine_dining':
                    return redirect("token-dashboard")
                return redirect("/tables/")
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, "accounts/login.html")


def logout_view(request):
    logout(request)
    return redirect("login")



@login_required
@tenant_required
def owner_dashboard(request):

    if request.user.role not in ["owner", "manager", "cashier"]:
        return HttpResponseForbidden()

    metrics = owner_dashboard_metrics(request.user)

    notifications = Notification.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_read=False
    ).order_by("-created_at")[:10]

    from setup.models import AggregatorConfig
    aggregator_config, _ = AggregatorConfig.objects.get_or_create(
        tenant=request.user.tenant,
        outlet=request.user.outlet
    )

    # ── QSR routing context ───────────────────────────────────────────
    tenant = request.user.tenant
    is_qsr = tenant.tenant_type in ["franchise", "cafe"]

    # direct_billing_mode: if enabled the Core Ops card skips the token
    # dashboard and goes straight to billing (creates a token & redirects).
    from core.features import has_feature
    direct_billing_mode = is_qsr and has_feature(tenant, "direct_billing_mode")

    # Live badge: how many open tokens are there right now?
    active_token_count = 0
    if is_qsr:
        from orders.models import TokenOrder
        from core.utils import get_business_date
        from django.utils import timezone
        today = get_business_date(timezone.now(), request.user.outlet)
        active_token_count = TokenOrder.objects.filter(
            outlet=request.user.outlet,
            date=today,
            order__status__in=["open", "billing"],
        ).count()

    # ── Role-aware card visibility ────────────────────────────────────
    is_manager = request.user.role == "manager"
    is_cashier = request.user.role == "cashier"

    # Cashiers are only allowed on this dashboard if direct_billing_mode is active
    if is_cashier and not direct_billing_mode:
        return redirect("token-dashboard")

    return render(
        request,
        "accounts/owner_dashboard.html",
        {
            "metrics":              metrics,
            "notifications":        notifications,
            "aggregator":           aggregator_config,
            "is_qsr":               is_qsr,
            "direct_billing_mode":  direct_billing_mode,
            "active_token_count":   active_token_count,
            "is_manager":           is_manager,
            "is_cashier":           is_cashier,
        }
    )

@login_required
@tenant_required
def dashboard_metrics_json(request):
    """AJAX endpoint — returns today's metrics as JSON for live refresh."""
    if request.user.role not in ["owner", "manager", "cashier"]:
        return JsonResponse({"error": "forbidden"}, status=403)
    metrics = owner_dashboard_metrics(request.user)
    return JsonResponse({"metrics": metrics})


@login_required
def sales_dashboard(request):
    """
    Shows all clients (Tenants). 
    If superuser: sees all, can add/remove/allocate.
    Otherwise: sees only clients they are the sales_agent for.
    """
    from tenants.models import Tenant
    from accounts.models import User
    
    if request.method == "POST" and request.user.is_superuser:
        action = request.POST.get("action")
        
        if action == "add_client":
            name = request.POST.get("name")
            agent_id = request.POST.get("agent_id")
            if name:
                agent = User.objects.filter(id=agent_id).first() if agent_id else None
                Tenant.objects.create(name=name, sales_agent=agent)
                messages.success(request, f"Client {name} added successfully.")
        
        elif action == "allocate_client":
            tenant_id = request.POST.get("tenant_id")
            agent_id = request.POST.get("agent_id")
            tenant = Tenant.objects.filter(id=tenant_id).first()
            if tenant:
                if agent_id:
                    agent = User.objects.filter(id=agent_id).first()
                    tenant.sales_agent = agent
                    tenant.save()
                    messages.success(request, f"Allocated {agent.username} to {tenant.name}.")
                else:
                    tenant.sales_agent = None
                    tenant.save()
                    messages.success(request, f"Removed allocation for {tenant.name}.")

        elif action == "delete_client":
            tenant_id = request.POST.get("tenant_id")
            tenant = Tenant.objects.filter(id=tenant_id).first()
            if tenant:
                name = tenant.name
                tenant.is_active = False
                tenant.save(update_fields=["is_active"])
                messages.success(request, f"Client {name} deactivated.")
                
        return redirect("sales_dashboard")

    if request.user.is_superuser:
        clients = Tenant.objects.all().select_related("sales_agent")
        agents = User.objects.filter(is_superuser=False) # Potential agents
    else:
        clients = Tenant.objects.filter(sales_agent=request.user)
        agents = []
        if not clients.exists():
            return redirect("dashboard")

    return render(
        request,
        "accounts/sales_dashboard.html",
        {"clients": clients, "agents": agents}
    )


# ------------------------------------------------------------------
# FEATURE FLAG MANAGEMENT UI  (superuser only)
# ------------------------------------------------------------------

# Single source of truth for every feature name → UI metadata.
# Matches FEATURE_GROUPS in core/features.py — keep both in sync.
_FEATURE_META = {
    # Ordering & Billing
    "floor_plan":           {"label": "Floor Plan",           "icon": "bi-grid-3x3",        "desc": "Visual table map for dine-in. Waiters pick tables here.", "group": "Ordering & Billing"},
    "token_system":         {"label": "Token Ordering",       "icon": "bi-123",             "desc": "QSR counter tokens — physical and online queues.", "group": "Ordering & Billing"},
    "simple_billing":       {"label": "Simple Billing",       "icon": "bi-lightning-charge", "desc": "Stripped-down billing screen without table selection.", "group": "Ordering & Billing"},
    "direct_billing_mode":  {"label": "Direct Billing Mode",  "icon": "bi-arrow-right-circle","desc": "Cashiers skip token dashboard and land directly on billing.", "group": "Ordering & Billing"},
    "split_bill":           {"label": "Split Bill",           "icon": "bi-scissors",         "desc": "Divide a bill between multiple guests or payment methods.", "group": "Ordering & Billing"},
    "running_order":        {"label": "Running Order View",   "icon": "bi-list-check",       "desc": "Live sidebar showing all active order items for floor staff.", "group": "Ordering & Billing"},
    "merge_tables":         {"label": "Merge Tables",         "icon": "bi-diagram-3",        "desc": "Combine two or more tables into a single order.", "group": "Ordering & Billing"},
    "qr_menu":              {"label": "Digital QR Menu",      "icon": "bi-qr-code",          "desc": "Customer-facing menu via QR scan. Supports self-ordering.", "group": "Ordering & Billing"},
    "modifiers":            {"label": "Item Modifiers",       "icon": "bi-sliders",          "desc": "Add-ons and customisations per menu item (e.g. no onions).", "group": "Ordering & Billing"},
    "platform_sync":        {"label": "Platform Sync",        "icon": "bi-arrow-left-right", "desc": "Toggle menu item availability on Zomato and Swiggy.", "group": "Ordering & Billing"},
    # Kitchen
    "kot_system":           {"label": "KOT Printing",         "icon": "bi-printer",          "desc": "Kitchen Order Tickets printed on order confirmation.", "group": "Kitchen"},
    "kitchen_display":      {"label": "Kitchen Display (KDS)","icon": "bi-display",          "desc": "Real-time KDS screen for chefs — replaces paper tickets.", "group": "Kitchen"},
    "multi_kitchen":        {"label": "Multi-Kitchen Stations","icon": "bi-diagram-2",       "desc": "Route items to multiple kitchen stations (grill, cold, bar).", "group": "Kitchen"},
    "waiter_call":          {"label": "Waiter Call (QR)",     "icon": "bi-bell",             "desc": "Customers summon a waiter by scanning their table QR.", "group": "Kitchen"},
    # Inventory
    "inventory":            {"label": "Inventory",            "icon": "bi-boxes",            "desc": "Real-time stock tracking with auto-deduction on orders.", "group": "Inventory"},
    "barcode_transfer":     {"label": "Barcode Transfer",     "icon": "bi-upc-scan",         "desc": "Transfer pre-made items from central kitchen via barcode.", "group": "Inventory"},
    "purchase_orders":      {"label": "Purchase Orders",      "icon": "bi-cart-check",       "desc": "Raise and track POs to vendors. Receive stock directly.", "group": "Inventory"},
    # Reports
    "reports":              {"label": "Reports & Analytics",  "icon": "bi-bar-chart-line",   "desc": "Sales, item, and shift reports with CSV export.", "group": "Reports"},
    "gstr_export":          {"label": "GSTR-1 Export",        "icon": "bi-file-earmark-spreadsheet", "desc": "Export GSTR-1 compatible CSV for GST filing.", "group": "Reports"},
    "advanced_reports":     {"label": "Advanced Reports",     "icon": "bi-graph-up-arrow",   "desc": "Hourly trends, category deep-dives, and custom date ranges.", "group": "Reports"},
    # CRM & Loyalty
    "crm":                  {"label": "CRM / Guests",         "icon": "bi-people",           "desc": "Guest profiles, visit history, and spend tracking.", "group": "CRM & Loyalty"},
    "reservations":         {"label": "Reservations",         "icon": "bi-calendar-check",   "desc": "Table booking with date, time, and guest count.", "group": "CRM & Loyalty"},
    "loyalty_points":       {"label": "Loyalty Points",       "icon": "bi-star",             "desc": "Earn and redeem points per order. Custom multiplier per tier.", "group": "CRM & Loyalty"},
    # Menu
    "ai_menu_import":       {"label": "AI Menu Import",       "icon": "bi-stars",            "desc": "Photograph any printed menu — AI imports all items in 60s.", "group": "Menu"},
}


@login_required
def feature_flags_view(request):
    """
    Superuser-only UI for managing feature overrides per tenant.

    Superusers pick a tenant via ?tenant_id=<id> query param.
    If no tenant_id is given, shows a tenant selector page.
    """
    from core.features import TENANT_FEATURES, FEATURE_GROUPS
    from tenants.models import TenantFeatureOverride

    if not request.user.is_superuser:
        return HttpResponseForbidden("Feature management is restricted to Rasova staff.")

    from tenants.models import Tenant
    all_tenants = Tenant.objects.order_by("name")

    tenant_id = request.GET.get("tenant_id")
    if not tenant_id:
        return render(request, "accounts/feature_flags.html", {
            "tenant": None,
            "all_tenants": all_tenants,
        })

    try:
        tenant = Tenant.objects.get(id=tenant_id)
    except Tenant.DoesNotExist:
        return HttpResponseForbidden("Tenant not found.")

    tenant_type = tenant.tenant_type
    overrides = {o.feature: o for o in TenantFeatureOverride.objects.filter(tenant=tenant)}
    default_features = set(TENANT_FEATURES.get(tenant_type, TENANT_FEATURES["fine_dining"]))

    grouped_features = {}
    for group_name, feature_keys in FEATURE_GROUPS.items():
        group_items = []
        for key in feature_keys:
            meta = _FEATURE_META.get(key)
            if not meta:
                continue
            override = overrides.get(key)
            if override is not None:
                state = "on" if override.enabled else "off"
                source = "override"
            elif key in default_features:
                state = "on"
                source = "default"
            else:
                state = "off"
                source = "default"
            group_items.append({
                "key": key,
                "label": meta["label"],
                "icon": meta["icon"],
                "desc": meta["desc"],
                "state": state,
                "source": source,
            })
        if group_items:
            grouped_features[group_name] = group_items

    return render(request, "accounts/feature_flags.html", {
        "grouped_features": grouped_features,
        "tenant": tenant,
        "tenant_type_display": tenant_type.replace("_", " ").title(),
        "all_tenants": all_tenants,
    })


@login_required
@require_POST
def toggle_feature_flag(request):
    """
    AJAX endpoint: creates/removes a TenantFeatureOverride for one feature.

    Body: { "tenant_id": 5, "feature": "token_system", "enabled": true }

    Superuser only. If toggling back to the type default, the override row is
    deleted so the DB stays clean (no redundant rows).
    """
    import json
    from tenants.models import TenantFeatureOverride, Tenant
    from core.features import TENANT_FEATURES

    if not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    tenant_id = data.get("tenant_id")
    feature = data.get("feature", "").strip()
    enabled = data.get("enabled")

    if not tenant_id:
        return JsonResponse({"error": "tenant_id is required"}, status=400)
    if not feature or feature not in _FEATURE_META:
        return JsonResponse({"error": f"Unknown feature: {feature!r}"}, status=400)
    if enabled is None:
        return JsonResponse({"error": "enabled field required"}, status=400)

    try:
        tenant = Tenant.objects.get(id=tenant_id)
    except Tenant.DoesNotExist:
        return JsonResponse({"error": "Tenant not found"}, status=404)

    default_features = set(TENANT_FEATURES.get(tenant.tenant_type, TENANT_FEATURES["fine_dining"]))
    is_default_on = feature in default_features

    if bool(enabled) == is_default_on:
        # Toggling back to the type default — delete the override row (cleaner DB).
        TenantFeatureOverride.objects.filter(tenant=tenant, feature=feature).delete()
        source = "default"
    else:
        TenantFeatureOverride.objects.update_or_create(
            tenant=tenant,
            feature=feature,
            defaults={
                "enabled": enabled,
                "notes": f"Set via UI by {request.user.username}",
            },
        )
        source = "override"

    # Bust the per-instance cache so has_feature() sees the change immediately.
    if hasattr(tenant, "_feature_overrides"):
        del tenant._feature_overrides

    return JsonResponse({
        "success": True,
        "feature": feature,
        "enabled": bool(enabled),
        "source": source,
    })