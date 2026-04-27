from django.conf import settings
from tenants.models import Tenant
from .log_filters import set_current_tenant_outlet, clear_current_tenant_outlet


class TenantMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]
        parts = host.split(".")

        tenant = None

        # --- Primary: real subdomain (production path) ---
        if len(parts) > 2 and not parts[0].replace("-", "").isdigit():
            subdomain = parts[0]
            try:
                tenant = Tenant.objects.get(slug=subdomain, is_active=True)
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