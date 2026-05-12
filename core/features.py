# core/features.py

# -----------------------------------------------------------------------------
# DEFAULT FEATURES PER TENANT TYPE
# These are the baseline features a tenant gets based on their restaurant type.
# Use TenantFeatureOverride to add/remove features for individual tenants.
# -----------------------------------------------------------------------------

TENANT_FEATURES = {
    'fine_dining': [
        # Ordering & tables
        'floor_plan',
        'merge_tables',
        'running_order',
        'split_bill',
        'modifiers',
        'platform_sync',
        'waiter_call',
        # Kitchen
        'kot_system',
        'kitchen_display',
        # Menu & ordering
        'qr_menu',
        'ai_menu_import',
        # CRM
        'crm',
        'reservations',
        # Operations
        'inventory',
        'purchase_orders',
        'reports',
        'role_based_access',
        'multi_outlet',
        'shift_management',
    ],
    'franchise': [
        # Ordering
        'token_system',
        'simple_billing',
        'barcode_transfer',
        # Kitchen
        'kot_system',
        'kitchen_display',
        # Menu
        'ai_menu_import',
        # Operations
        'inventory',
        'purchase_orders',
        'reports',
        'role_based_access',
        'multi_outlet',
        'shift_management',
    ],
    'cafe': [
        # Ordering
        'token_system',
        'simple_billing',
        # Kitchen
        'kot_system',
        'kitchen_display',
        # Menu
        'qr_menu',
        'ai_menu_import',
        # Operations
        'inventory',
        'purchase_orders',
        'reports',
        'role_based_access',
        'multi_outlet',
        'shift_management',
    ],
}

# -----------------------------------------------------------------------------
# FEATURE GROUPS — used for the UI to display features by category.
# Features listed here that are not in TENANT_FEATURES are "custom only"
# (can only be enabled via TenantFeatureOverride, never on by default).
# -----------------------------------------------------------------------------

FEATURE_GROUPS = {
    'Ordering & Billing': [
        'floor_plan',
        'token_system',
        'simple_billing',
        'direct_billing_mode',
        'split_bill',
        'running_order',
        'merge_tables',
        'qr_menu',
        'modifiers',
        'platform_sync',
    ],
    'Kitchen': [
        'kot_system',
        'kitchen_display',
        'multi_kitchen',
        'waiter_call',
    ],
    'Inventory': [
        'inventory',
        'barcode_transfer',
        'purchase_orders',
    ],
    'Reports': [
        'reports',
        'gstr_export',
        'advanced_reports',
    ],
    'CRM & Loyalty': [
        'crm',
        'reservations',
        'loyalty_points',
    ],
    'Menu': [
        'ai_menu_import',
    ],
}


def has_feature(tenant, feature):
    """
    Returns True if the given tenant has the given feature enabled.

    Resolution order:
      1. TenantFeatureOverride — explicit per-tenant on/off. If an override
         exists, it wins regardless of the tenant type default.
      2. TENANT_FEATURES[tenant.tenant_type] — the baseline feature set for
         the tenant's restaurant type.

    Overrides are cached on the tenant instance (_feature_overrides) for the
    lifetime of the request to prevent N+1 queries. The cache is busted by
    toggle_feature_flag after each change.

    Edge cases:
      - tenant is None            → False (unauthenticated / no tenant assigned)
      - tenant.tenant_type unknown → falls back to fine_dining defaults
      - feature is unknown         → False (not in any default, no override)
    """
    if not tenant:
        return False

    # Superusers always have all features (makes testing and support easier).
    # Check this on the tenant object isn't the right place — callers should
    # check request.user.is_superuser. But has_feature is sometimes called
    # without a request, so we don't enforce that here.

    # Cache overrides on the tenant instance to prevent N+1 per request.
    if not hasattr(tenant, '_feature_overrides'):
        from tenants.models import TenantFeatureOverride
        overrides = TenantFeatureOverride.objects.filter(tenant=tenant)
        tenant._feature_overrides = {o.feature: o.enabled for o in overrides}

    if feature in tenant._feature_overrides:
        return tenant._feature_overrides[feature]

    # No override — use the type default.
    defaults = TENANT_FEATURES.get(tenant.tenant_type, TENANT_FEATURES['fine_dining'])
    return feature in defaults


def get_all_known_features():
    """Returns the flat set of every feature name known to the system."""
    all_features = set()
    for features in FEATURE_GROUPS.values():
        all_features.update(features)
    return all_features
