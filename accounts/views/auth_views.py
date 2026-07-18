"""Login and logout."""
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect
from django_ratelimit.decorators import ratelimit


def _role_path(user):
    """Return the path the user should land on after login."""
    tenant_type = user.tenant.tenant_type if user.tenant else "fine_dining"
    if user.role in ("owner", "manager"):
        return "/dashboard/"
    if user.role == "agent":
        return "/sales/"
    if user.role in ("waiter", "captain"):
        return "/tables/" if tenant_type == "fine_dining" else "/token/"
    if user.role == "chef":
        return "/kitchen/"
    if user.role == "cashier":
        if tenant_type != "fine_dining":
            from core.features import has_feature
            if has_feature(user.tenant, "direct_billing_mode"):
                return "/dashboard/"
            return "/token/"
        return "/billing/"
    return "/tables/" if tenant_type == "fine_dining" else "/token/"


def _subdomain_redirect(user, path):
    """
    Build an absolute URL to the user's subdomain.
    Returns None in DEBUG mode (no subdomains locally).
    """
    if settings.DEBUG:
        return None
    tenant = user.tenant
    if not tenant or not tenant.slug:
        return None
    base = settings.BASE_URL.rstrip("/")           # e.g. https://rasova.net
    proto, rest = base.split("://", 1)
    domain = rest.lstrip("www.").split("/")[0]     # rasova.net
    return f"{proto}://{tenant.slug}.{domain}{path}"


@ratelimit(key="ip", rate="10/m", method="POST", block=False)
def login_view(request):
    if getattr(request, "limited", False):
        messages.error(request, "Too many login attempts. Please wait a minute.")
        return render(request, "accounts/login.html", status=429)

    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            path = _role_path(user)
            url  = _subdomain_redirect(user, path)
            return redirect(url or path)

        messages.error(request, "Invalid username or password.")

    return render(request, "accounts/login.html", {
        "tenant": getattr(request, "tenant", None),
    })


def logout_view(request):
    logout(request)
    return redirect("login")
