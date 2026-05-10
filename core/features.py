# core/features.py

TENANT_FEATURES = {
    'fine_dining': [
        'floor_plan',
        'kot_system',
        'waiter_call',
        'qr_menu',
        'reservations',
        'crm',
        'split_bill',
        'inventory',
        'reports',
        'merge_tables',
        'running_order',
        'kitchen_display',
    ],
    'franchise': [
        'token_system',
        'kot_system',
        'inventory',
        'barcode_transfer',
        'reports',
        'simple_billing',
    ],
    'cafe': [
        'token_system',
        'simple_billing',
        'qr_menu',
        'inventory',
        'reports',
    ],
}

def has_feature(tenant, feature):
    if not tenant:
        return False
    features = TENANT_FEATURES.get(tenant.tenant_type, TENANT_FEATURES['fine_dining'])
    return feature in features
