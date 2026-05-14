"""Login and logout."""
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect


def login_view(request):
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
