#core/middleware.py
from tenants.models import Tenant


class TenantMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]
        parts = host.split(".")

        tenant = None

        # --- Primary: real subdomain (production path) ---
        # Requires at least 3 parts (sub.domain.tld) and a non-numeric first part
        if len(parts) > 2 and not parts[0].replace("-", "").isdigit():
            subdomain = parts[0]
            try:
                tenant = Tenant.objects.get(slug=subdomain, is_active=True)
            except Tenant.DoesNotExist:
                tenant = None

        # --- Fallback: ?tenant=slug query param or session (dev / IP access) ---
        if tenant is None:
            slug = request.GET.get("tenant") or request.session.get("dev_tenant_slug")
            if slug:
                try:
                    tenant = Tenant.objects.get(slug=slug, is_active=True)
                    # Persist so subsequent requests in the same session don't need the param
                    request.session["dev_tenant_slug"] = slug
                except Tenant.DoesNotExist:
                    tenant = None

        request.tenant = tenant
        response = self.get_response(request)
        return response