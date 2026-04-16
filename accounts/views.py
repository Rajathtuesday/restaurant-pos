# accounts/views.py
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import HttpResponseForbidden
from notifications.models import Notification
from reports.services.dashboard_metrics import owner_dashboard_metrics
from django.shortcuts import redirect

def login_view(request):

    if request.method == "POST":

        username = request.POST["username"]
        password = request.POST["password"]

        user = authenticate(request, username=username, password=password)

        if user is not None:

            login(request, user)

            # ROLE BASED REDIRECT
            if user.role in ["owner", "manager"]:
                return redirect("/dashboard/")

            elif user.role == "waiter":
                return redirect("/tables/")

            elif user.role == "chef":
                return redirect("/kitchen/")

            elif user.role == "cashier":
                return redirect("/billing/")

            else:
                return redirect("/tables/")
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, "accounts/login.html")


def logout_view(request):
    logout(request)
    return redirect("login")



@login_required
def owner_dashboard(request):

    if request.user.role not in ["owner","manager"]:
        return HttpResponseForbidden()

    metrics = owner_dashboard_metrics(request.user)

    notifications = Notification.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        is_read=False
    ).order_by("-created_at")[:10]

    return render(
        request,
        "accounts/owner_dashboard.html",
        {
            "metrics": metrics,
            "notifications": notifications
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
                tenant.delete()
                messages.success(request, f"Client {name} deleted.")
                
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