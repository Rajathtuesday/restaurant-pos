#core/middleware.py
from tenants.models import Tenant


class TenantMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):

        host = request.get_host().split(":")[0]

        parts = host.split(".")

        if len(parts) > 2:

            subdomain = parts[0]

            try:
                tenant = Tenant.objects.get(slug=subdomain)
                request.tenant = tenant
            except Tenant.DoesNotExist:
                request.tenant = None

        else:
            request.tenant = None

        response = self.get_response(request)

        return response