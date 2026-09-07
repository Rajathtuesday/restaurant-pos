"""Login and logout."""
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect
from django_ratelimit.decorators import ratelimit

from accounts.models import User


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


@ratelimit(key="ip", rate="20/m", method="GET", block=False)
def demo_login(request):
    """
    Public, unauthenticated entry point: logs a visitor straight into the
    Demo Bistro tenant's owner account and lands them on the live floor
    plan, no signup or typed credentials at all.

    Deliberately not a shared *typed* password -- this app locks an
    account out after 5 failed logins (AXES_FAILURE_LIMIT), and a public
    password that strangers type by hand will eventually get mistyped,
    locking the demo for everyone until the hour-long cooldown clears. A
    magic link has no failed-password attempt to count against anything.

    Rate-limited per IP (not the tighter 10/m real login gets) since this
    has no password to brute-force in the first place -- the limit here is
    purely to stop someone scripting repeated hits, not a security control
    on the account itself. The demo owner account also carries an
    unusable password (see demo_seed.py), so it can never be reached
    through the normal /login/ form even if someone guessed the username.
    """
    if getattr(request, "limited", False):
        messages.error(request, "Too many requests. Please wait a minute and try again.")
        return redirect("landing")

    from orders.scripts.demo_seed import DEMO_OWNER_USERNAME

    try:
        demo_owner = User.objects.get(username=DEMO_OWNER_USERNAME, role="owner")
    except User.DoesNotExist:
        messages.error(request, "The live demo isn't set up yet. Please check back shortly.")
        return redirect("landing")

    # Explicit backend required: this project has two AUTHENTICATION_BACKENDS
    # configured (ModelBackend + axes), and login() can only infer which one
    # to attach to the session when the user object already carries a
    # `.backend` attribute -- which only happens as a side effect of going
    # through authenticate() first. A magic link has no password to check,
    # so there's nothing to authenticate() against; ModelBackend is the
    # correct one to attach directly, same backend a normal password login
    # ends up using anyway.
    login(request, demo_owner, backend="django.contrib.auth.backends.ModelBackend")
    path = "/tables/"  # the live floor plan -- the most immediately convincing view, not the analytics dashboard
    url = _subdomain_redirect(demo_owner, path)
    return redirect(url or path)
