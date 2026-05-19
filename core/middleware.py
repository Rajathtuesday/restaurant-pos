import time
import logging

from django.conf import settings
from tenants.models import Tenant
from .log_filters import set_current_tenant_outlet, clear_current_tenant_outlet

request_logger = logging.getLogger("pos.core")


class TenantMiddleware:

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
        
        # Initial set of tenant context (outlet will be NA until authenticated)
        tenant_id = tenant.id if tenant else 'NA'
        set_current_tenant_outlet(tenant_id, 'NA')
        
        response = self.get_response(request)
        return response


class ContextLoggingMiddleware:
    """
    Middleware that runs AFTER authentication to capture the outlet ID for logs.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Update context if user is authenticated and has an outlet
        tenant_id = getattr(request, 'tenant', None)
        tenant_id = tenant_id.id if tenant_id else 'NA'
        
        outlet_id = 'NA'
        if request.user.is_authenticated:
            if hasattr(request.user, 'outlet') and request.user.outlet:
                outlet_id = request.user.outlet.id
            if not tenant_id and hasattr(request.user, 'tenant') and request.user.tenant:
                tenant_id = request.user.tenant.id

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