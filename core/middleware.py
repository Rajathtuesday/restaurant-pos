import time
import logging

from django.conf import settings
from django.shortcuts import render
from tenants.models import Tenant
from .tenant_context import set_current_tenant_outlet, clear_current_tenant_outlet

request_logger = logging.getLogger("pos.core")


class TenantMiddleware:
    """
    Resolves request.tenant from the hostname, for host/branding/routing
    purposes only. Deliberately does NOT touch the tenant query-scoping
    context (see core/tenant_context.py) -- a session cookie is valid
    across every *.rasova.net subdomain (SESSION_COOKIE_DOMAIN), and the
    superuser/portal panel is reachable on any of them, not host-restricted.
    Scoping queries off the subdomain-resolved tenant would make a
    superuser managing tenant B while physically on tenant A's subdomain
    silently require both at once -- an impossible condition that returns
    an empty queryset, not an error. ContextLoggingMiddleware, which runs
    after authentication and scopes off request.user.tenant instead, is
    the only thing that sets that context now.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]
        parts = host.split(".")

        tenant = None

        # --- Primary: real subdomain (production path) ---
        if len(parts) > 2 and not parts[0].replace("-", "").isdigit():
            subdomain = parts[0].lower()   # slugs are always lowercase
            try:
                tenant = Tenant.objects.get(slug__iexact=subdomain, is_active=True)
            except Tenant.DoesNotExist:
                tenant = None

        # --- Fallback: ?tenant=slug query param or session (dev / IP access) ---
        if tenant is None and settings.DEBUG:
            slug = request.GET.get("tenant") or request.session.get("dev_tenant_slug")
            if slug:
                try:
                    tenant = Tenant.objects.get(slug=slug, is_active=True)
                    request.session["dev_tenant_slug"] = slug
                except Tenant.DoesNotExist:
                    tenant = None

        request.tenant = tenant

        response = self.get_response(request)
        return response


class SubscriptionStatusMiddleware:
    """
    Blocks access for tenants whose subscription_status is 'suspended'.
    Must run after AuthenticationMiddleware (needs request.user) and after
    TenantMiddleware (needs request.tenant).

    Only gates already-authenticated, non-superuser requests. Anonymous
    requests pass through untouched — this is deliberate, not an oversight:
    gating before login too would also block a superuser from reaching the
    login page on a suspended tenant's subdomain to investigate/support it,
    since superuser status can't be known until after they've logged in.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = getattr(request, "tenant", None)
        if (
            tenant
            and tenant.subscription_status == "suspended"
            and request.user.is_authenticated
            and not request.user.is_superuser
            and request.path != "/logout/"
        ):
            return render(request, "tenants/suspended.html", {"tenant": tenant}, status=402)
        return self.get_response(request)


class ContextLoggingMiddleware:
    """
    The single setter and clearer of core.tenant_context, which
    TenantManager reads to auto-scope every TenantScopedModel query (see
    core/models.py). Runs after authentication and scopes off
    request.user.tenant/request.user.outlet, NOT request.tenant -- see
    the docstring on TenantMiddleware above for why that distinction is
    load-bearing, not stylistic. Superuser accounts have tenant=None, so
    this naturally, correctly leaves them unscoped with no special-casing.

    Wrapped in try/finally so the context can never survive past the
    request that set it, even if the view raises -- this is the one
    thing that MUST hold for TenantManager's auto-filtering to be safe
    rather than a source of cross-request data leakage on a reused
    worker thread.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = None
        outlet_id = None
        if request.user.is_authenticated:
            if getattr(request.user, 'tenant', None):
                tenant_id = request.user.tenant_id
            if getattr(request.user, 'outlet', None):
                outlet_id = request.user.outlet_id

        set_current_tenant_outlet(tenant_id, outlet_id)

        try:
            response = self.get_response(request)
        finally:
            # Clear context after request finishes to prevent leak between threads
            clear_current_tenant_outlet()

        return response


class RequestLoggingMiddleware:
    """
    Logs every HTTP request: method, path, status, duration, user, tenant.
    Add after ContextLoggingMiddleware in MIDDLEWARE so user/tenant are already set.
    """

    # Paths not worth logging (health checks, static assets)
    _SKIP_PREFIXES = ("/static/", "/media/", "/health/", "/favicon.")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if any(request.path.startswith(p) for p in self._SKIP_PREFIXES):
            return self.get_response(request)

        t0 = time.monotonic()
        response = self.get_response(request)
        duration_ms = round((time.monotonic() - t0) * 1000)

        user = request.user
        username = user.username if user.is_authenticated else "anon"
        tenant_name = getattr(getattr(user, "tenant", None), "name", None)
        if not tenant_name:
            tenant_name = getattr(getattr(request, "tenant", None), "name", "–")

        status = response.status_code
        level = logging.WARNING if status >= 400 else logging.INFO

        request_logger.log(
            level,
            "%s %s %s  %dms  user=%s  tenant=%s",
            request.method,
            request.path,
            status,
            duration_ms,
            username,
            tenant_name,
        )

        return response