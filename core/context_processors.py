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
    
    from core.features import TENANT_FEATURES
    features = TENANT_FEATURES.get(tenant.tenant_type, TENANT_FEATURES['fine_dining'])
    
    return {
        'tenant_features': features,
        'tenant_type': tenant.tenant_type,
    }
