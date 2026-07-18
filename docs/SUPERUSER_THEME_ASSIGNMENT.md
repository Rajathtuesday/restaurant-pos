# Superuser Theme & Tier Assignment

## What This Is

A Django view accessible only to superusers that lets them:

1. Set a tenant's **subscription tier** (Starter / Professional / Enterprise)
2. Set a tenant's **theme** (constrained by their tier)
3. Toggle individual **feature flags** (existing `feature_flags_view`)
4. View the tenant's current plan and expiry date

---

## URL

```
/superuser/tenant/<tenant_id>/settings/
```

Only accessible when `request.user.is_superuser`. Any other user gets 404
(not 403 — we don't reveal the URL exists).

---

## View

```python
# accounts/views.py

from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.views.decorators.http import require_POST

TIER_ALLOWED_THEMES = {
    'starter':      ['standard', 'minimal'],
    'professional': ['standard', 'minimal', 'luxury'],
    'enterprise':   ['luxury', 'standard', 'qsr', 'minimal'],
}

def superuser_tenant_settings(request, tenant_id):
    if not request.user.is_superuser:
        from django.http import Http404
        raise Http404

    from tenants.models import Tenant
    tenant = get_object_or_404(Tenant, id=tenant_id)
    allowed_themes = TIER_ALLOWED_THEMES.get(tenant.subscription_tier, ['standard'])

    if request.method == 'POST':
        new_tier  = request.POST.get('subscription_tier')
        new_theme = request.POST.get('theme')

        valid_tiers  = [c[0] for c in Tenant.TIER_CHOICES]
        valid_themes = TIER_ALLOWED_THEMES.get(new_tier, ['standard'])

        if new_tier in valid_tiers:
            tenant.subscription_tier = new_tier
            allowed_themes = valid_themes

        if new_theme in allowed_themes:
            tenant.theme = new_theme
        else:
            # Theme not allowed for this tier — reset to default
            tenant.theme = 'standard'
            messages.warning(
                request,
                f"Theme '{new_theme}' is not available on {new_tier}. "
                f"Reset to Standard."
            )

        tenant.save(update_fields=['subscription_tier', 'theme'])
        # Bust feature override cache
        if hasattr(tenant, '_feature_overrides'):
            del tenant._feature_overrides

        messages.success(request, f"Settings updated for {tenant.name}.")
        return redirect('superuser_tenant_settings', tenant_id=tenant_id)

    from core.features import FEATURE_GROUPS, has_feature
    from tenants.models import TenantFeatureOverride

    overrides = {o.feature: o.enabled
                 for o in TenantFeatureOverride.objects.filter(tenant=tenant)}

    context = {
        'tenant': tenant,
        'allowed_themes': allowed_themes,
        'tier_choices': Tenant.TIER_CHOICES,
        'theme_choices': Tenant.THEME_CHOICES,
        'feature_groups': FEATURE_GROUPS,
        'overrides': overrides,
    }
    return render(request, 'accounts/superuser_tenant_settings.html', context)
```

---

## Template: superuser_tenant_settings.html

```
╔════════════════════════════════════════════════════════════╗
║  Tenant Settings — The Grand Spice                         ║
╠════════════════════════════════════════════════════════════╣
║                                                            ║
║  SUBSCRIPTION TIER                                         ║
║  ○ Starter       ₹999/mo    — Basic features               ║
║  ● Professional  ₹2,499/mo  — Full features, 3 outlets     ║
║  ○ Enterprise    ₹5,999/mo  — Unlimited                    ║
║                                                            ║
║  THEME  (for Professional: Standard, Minimal, Luxury)      ║
║  ┌──────────┐ ┌──────────┐ ┌──────────┐                    ║
║  │ Standard │ │ Minimal  │ │ LUXURY ✓ │                    ║
║  │ Blue/Wht │ │ Sage/Wam │ │ Gld/Blk  │                    ║
║  └──────────┘ └──────────┘ └──────────┘                    ║
║                                                            ║
║  FEATURES (overrides)                                      ║
║  Ordering & Billing                                        ║
║  [✓] floor_plan    [✓] split_bill    [ ] platform_sync     ║
║  Kitchen                                                   ║
║  [✓] kot_system    [✓] kitchen_display                     ║
║  ...                                                       ║
║                                                            ║
║  [  Save Settings  ]                                       ║
╚════════════════════════════════════════════════════════════╝
```

---

## URL Configuration

```python
# accounts/urls.py
path(
    'superuser/tenant/<int:tenant_id>/settings/',
    views.superuser_tenant_settings,
    name='superuser_tenant_settings'
),
```

---

## Tenant List for Superusers

Add a tenant list page so the superuser can find tenants without using Django
admin:

```
/superuser/tenants/
```

Shows a table:

| Tenant Name        | Type         | Tier         | Theme    | Actions       |
|--------------------|--------------|--------------|----------|---------------|
| The Grand Spice    | fine_dining  | Professional | luxury   | [Edit]        |
| Burger Blitz       | franchise    | Enterprise   | qsr      | [Edit]        |
| Bean & Brew        | cafe         | Starter      | minimal  | [Edit]        |

---

## Security Notes

- Views check `request.user.is_superuser` — not `is_staff`. Staff cannot
  access these pages.
- 404 is raised (not 403) to prevent URL enumeration.
- No AJAX on this form — plain POST for simplicity and auditability.
- All tier/theme values are validated against server-side allowlists before
  saving. Client-side filtering is UX only.
- The feature override form posts to the existing `toggle_feature_flag` view
  (already implemented) — no new endpoint needed.

---

## Migration Required

```bash
python manage.py makemigrations tenants --name add_tier_and_theme
python manage.py migrate
```

Fields added to `tenants_tenant`:
- `subscription_tier` VARCHAR(20) DEFAULT 'starter'
- `theme` VARCHAR(20) DEFAULT 'standard'
