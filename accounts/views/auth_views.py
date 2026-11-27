"""Login and logout."""
import secrets

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect
from django_ratelimit.decorators import ratelimit

from accounts.lockout import lockout_response
from accounts.models import User
from core.utils import get_client_ip


def _role_path(user):
    """Return the path the user should land on after login."""
    table_service = user.tenant.is_table_service if user.tenant else True
    if user.role in ("owner", "manager"):
        return "/dashboard/"
    if user.role == "agent":
        return "/sales/"
    if user.role in ("waiter", "captain"):
        return "/tables/" if table_service else "/token/"
    if user.role == "chef":
        return "/kitchen/"
    if user.role == "cashier":
        if not table_service:
            from core.features import has_feature
            if has_feature(user.tenant, "direct_billing_mode"):
                return "/dashboard/"
            return "/token/"
        return "/billing/"
    return "/tables/" if table_service else "/token/"


def _subdomain_redirect(user, path):
    """
    Build an absolute URL to the user's subdomain.
    Returns None in DEBUG mode (no subdomains locally) and on a box (one
    restaurant at one address: staff stay where they typed it).
    """
    if settings.DEBUG or getattr(settings, "BOX", False):
        return None
    tenant = user.tenant
    if not tenant or not tenant.slug:
        return None
    base = settings.BASE_URL.rstrip("/")           # e.g. https://rasova.net
    proto, rest = base.split("://", 1)
    domain = rest.lstrip("www.").split("/")[0]     # rasova.net
    return f"{proto}://{tenant.slug}.{domain}{path}"


def _name_at_address(group, request):
    """The rate limit's key for one name typed from one address."""
    return f"{get_client_ip(request)} {request.POST.get('username', '')}"


def login_rate_limits(view):
    """Two rate limits, both checked before any password, on every login
    page (the staff login and the admin's; they share the counters). One
    name from one address: 10 tries a minute, right or wrong (axes locks the
    name there at 5 wrong; this stops the page being hammered). One address,
    every name: 60 a minute, against trying many names from one place; a
    whole pub's shift change (every phone on its Wi-Fi shares one public
    address) never comes near it. Until 26 Nov 2026 it was 10 a minute per
    address with good logins counted, so the eleventh waiter at a shift
    change was turned away; the admin's login had none until 27 Nov 2026."""
    view = ratelimit(group="login-name", key=_name_at_address, rate="10/m", method="POST", block=False)(view)
    return ratelimit(group="login-address", key="ip", rate="60/m", method="POST", block=False)(view)


def _too_many_tries(request):
    messages.error(request, "Too many tries from this network. Wait a minute.")
    return render(request, "accounts/login.html", {"tenant": getattr(request, "tenant", None)}, status=429)


@login_rate_limits
def login_view(request):
    tenant = getattr(request, "tenant", None)
    if getattr(request, "limited", False):
        return _too_many_tries(request)

    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            path = _role_path(user)
            url  = _subdomain_redirect(user, path)
            return redirect(url or path)

        # Axes refused it as locked (accounts/lockout.py), whether or not
        # the password was right: say so, and when it opens.
        if getattr(request, "axes_locked_out", False):
            return lockout_response(request, getattr(request, "axes_credentials", None))

        messages.error(request, "Invalid username or password.")

    return render(request, "accounts/login.html", {"tenant": tenant})


@login_rate_limits
def admin_login(request):
    """The Django admin's login (at ADMIN_URL, core/urls.py), with the staff
    login's rate limits. Axes guards it as it guards every login."""
    if getattr(request, "limited", False):
        return _too_many_tries(request)
    from django.contrib import admin
    return admin.site.login(request)


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

    from orders.services.demo_seed import DEMO_OWNER_USERNAME

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

    # Trailer mode ON by default (the public link on the marketing page) --
    # OFF only for the one bookmarked link that carries the matching key,
    # e.g. when doing a live walkthrough for an actual restaurant owner.
    # compare_digest guards against a timing attack revealing the key
    # character-by-character; the `and` short-circuits so an unset
    # DEMO_FOUNDER_KEY (empty string) can never match an empty ?key= either.
    supplied_key = request.GET.get("key", "")
    is_founder = bool(settings.DEMO_FOUNDER_KEY) and secrets.compare_digest(supplied_key, settings.DEMO_FOUNDER_KEY)
    request.session["demo_trailer_mode"] = not is_founder

    path = "/tables/"  # the live floor plan -- the most immediately convincing view, not the analytics dashboard
    url = _subdomain_redirect(demo_owner, path)
    return redirect(url or path)
