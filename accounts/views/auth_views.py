"""Login and logout."""
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect
from django_ratelimit.decorators import ratelimit


@ratelimit(key="ip", rate="10/m", method="POST", block=False)
def login_view(request):
    # 10 POST attempts per minute per IP. block=False so we control the response.
    if getattr(request, "limited", False):
        messages.error(request, "Too many login attempts. Please wait a minute before trying again.")
        return render(request, "accounts/login.html", status=429)

    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            tenant_type = user.tenant.tenant_type if user.tenant else "fine_dining"

            if user.role in ["owner", "manager"]:
                return redirect("/dashboard/")
            elif user.role == "agent":
                return redirect("/sales/")
            elif user.role == "waiter":
                return redirect("/tables/") if tenant_type == "fine_dining" else redirect("token-dashboard")
            elif user.role == "chef":
                return redirect("/kitchen/")
            elif user.role == "cashier":
                if tenant_type != "fine_dining":
                    from core.features import has_feature
                    if has_feature(user.tenant, "direct_billing_mode"):
                        return redirect("/dashboard/")
                    return redirect("token-dashboard")
                return redirect("/billing/")
            else:
                return redirect("/tables/") if tenant_type == "fine_dining" else redirect("token-dashboard")
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, "accounts/login.html")


def logout_view(request):
    logout(request)
    return redirect("login")
