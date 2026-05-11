# accounts/views.py
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden

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