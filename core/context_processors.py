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
    
    from core.features import TENANT_FEATURES, has_feature
    from tenants.models import TenantFeatureOverride
    
    all_features = set()
    for f_list in TENANT_FEATURES.values():
        all_features.update(f_list)
        
    overrides = TenantFeatureOverride.objects.filter(tenant=tenant).values_list('feature', flat=True)
    all_features.update(overrides)

    resolved_features = [f for f in all_features if has_feature(tenant, f)]
    
    return {
        'tenant_features': resolved_features,
        'tenant_type': tenant.tenant_type,
    }

