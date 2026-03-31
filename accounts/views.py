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
    If superuser: sees all. 
    Otherwise: sees only clients they are the sales_agent for.
    """
    from tenants.models import Tenant
    if request.user.is_superuser:
        clients = Tenant.objects.all().select_related("sales_agent")
        # Give access to superuser
    else:
        clients = Tenant.objects.filter(sales_agent=request.user)
        # Give access to regular users ONLY if they are assigned a client
        if not clients.exists():
            return redirect("/dashboard/")

    return render(
        request,
        "accounts/sales_dashboard.html",
        {"clients": clients}
    )