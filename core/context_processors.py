# core/context_processors.py
from django.conf import settings

def base_url(request):
    return {'BASE_URL': settings.BASE_URL}

def tenant_features(request):
    if not request.user.is_authenticated:
        return {'tenant_features': [], 'tenant_type': 'fine_dining'}

    tenant = getattr(request.user, 'tenant', None)
    if not tenant:
        return {'tenant_features': [], 'tenant_type': 'fine_dining'}

    from core.features import has_feature, get_all_known_features

    # Use the canonical feature list from FEATURE_GROUPS so custom-only features
    # (multi_kitchen, loyalty_points, etc.) are evaluated for tenants that have
    # overrides enabling them — not just the TENANT_FEATURES defaults.
    resolved_features = [f for f in get_all_known_features() if has_feature(tenant, f)]

    return {
        'tenant_features': resolved_features,
        'tenant_type': tenant.tenant_type,
    }

