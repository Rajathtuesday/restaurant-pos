# Subscription Tiers

## Overview

Three tiers control what features and themes a tenant can access. The
superuser assigns a tier to each tenant. Tier enforcement is on top of the
existing `has_feature()` system — tier limits which *features* can be
enabled, and which *themes* are available.

---

## Tier Definitions

### Starter — ₹999/month

Entry level. For single-outlet small restaurants, cafes, dhabas.

| Category       | Included                                                  |
|----------------|-----------------------------------------------------------|
| Outlets        | 1 outlet only                                             |
| Themes         | `standard`, `minimal`                                     |
| Ordering       | Simple billing, token system                              |
| Kitchen        | KOT system                                                |
| Inventory      | Basic inventory (no purchase orders)                      |
| Reports        | Basic reports (daily totals only)                         |
| Staff          | Up to 5 users                                             |
| Not included   | Floor plan, multi-outlet, advanced reports, CRM, shifts,  |
|                | purchase orders, GST export, barcode transfer             |

---

### Professional — ₹2,499/month

Mid-tier. For growing restaurants and small chains.

| Category       | Included                                                  |
|----------------|-----------------------------------------------------------|
| Outlets        | Up to 3 outlets                                           |
| Themes         | `standard`, `minimal`, `luxury`                           |
| Ordering       | All ordering features (floor plan, QR menu, modifiers,    |
|                | split bill, merge tables, running order)                  |
| Kitchen        | KOT + kitchen display + multi-kitchen                     |
| Inventory      | Full inventory + purchase orders                          |
| Reports        | Full reports + GST export                                 |
| CRM            | CRM + reservations                                        |
| Staff          | Up to 20 users, role-based access, shift management       |
| Not included   | `qsr` theme, barcode transfer, advanced reports,          |
|                | loyalty points, platform sync                             |

---

### Enterprise — ₹5,999/month

Full access. For franchise chains and large multi-outlet operations.

| Category       | Included                                                  |
|----------------|-----------------------------------------------------------|
| Outlets        | Unlimited outlets                                         |
| Themes         | All 4 themes (`luxury`, `standard`, `qsr`, `minimal`)     |
| All features   | Every feature in the system                               |
| Staff          | Unlimited users                                           |
| Extras         | Barcode transfer, central kitchen production, advanced    |
|                | reports, loyalty points, platform sync, AI menu import   |

---

## Tier-to-Theme Mapping

| Theme     | Starter | Professional | Enterprise |
|-----------|---------|--------------|------------|
| standard  | Yes     | Yes          | Yes        |
| minimal   | Yes     | Yes          | Yes        |
| luxury    | No      | Yes          | Yes        |
| qsr       | No      | No           | Yes        |

The `qsr` theme is Enterprise-only because the QSR feature set (token system,
kitchen display, barcode transfer, multi-outlet routing) is designed for
franchise-scale operations.

---

## Tier-to-Feature Mapping

Features available per tier (additive — Enterprise includes all of Professional,
which includes all of Starter):

### Starter features
- `simple_billing`
- `token_system`
- `kot_system`
- `inventory`
- `reports`
- `role_based_access`

### Professional adds
- `floor_plan`
- `merge_tables`
- `running_order`
- `split_bill`
- `modifiers`
- `qr_menu`
- `kitchen_display`
- `multi_kitchen`
- `purchase_orders`
- `crm`
- `reservations`
- `multi_outlet`
- `shift_management`
- `gstr_export`
- `ai_menu_import`
- `waiter_call`

### Enterprise adds
- `platform_sync`
- `barcode_transfer`
- `advanced_reports`
- `loyalty_points`
- `direct_billing_mode`

---

## Data Model

Add to `tenants/models.py`:

```python
class Tenant(models.Model):
    TIER_CHOICES = [
        ('starter',      'Starter — ₹999/mo'),
        ('professional', 'Professional — ₹2,499/mo'),
        ('enterprise',   'Enterprise — ₹5,999/mo'),
    ]
    THEME_CHOICES = [
        ('luxury',   'Luxury'),
        ('standard', 'Standard'),
        ('qsr',      'QSR'),
        ('minimal',  'Minimal'),
    ]

    # existing fields ...
    subscription_tier = models.CharField(
        max_length=20,
        choices=TIER_CHOICES,
        default='starter'
    )
    theme = models.CharField(
        max_length=20,
        choices=THEME_CHOICES,
        default='luxury'      # ALL tenants start on luxury
    )
    subscription_active = models.BooleanField(default=True)
    subscription_expires = models.DateField(null=True, blank=True)
```

---

## Enforcement

### Theme enforcement (superuser assignment page)

When assigning a theme, the UI only shows themes allowed for the tenant's tier:

```python
TIER_ALLOWED_THEMES = {
    'starter':      ['standard', 'minimal'],
    'professional': ['standard', 'minimal', 'luxury'],
    'enterprise':   ['luxury', 'standard', 'qsr', 'minimal'],
}
```

If a tenant is downgraded and their current theme is no longer allowed, reset
to `standard`.

### Feature enforcement

The existing `has_feature()` resolver handles per-tenant feature access. Tier
restricts which features the superuser *can* enable via `TenantFeatureOverride`.
Add a tier-based guard to the feature flags admin view:

```python
TIER_ALLOWED_FEATURES = {
    'starter': ['simple_billing', 'token_system', 'kot_system',
                'inventory', 'reports', 'role_based_access'],
    'professional': [...starter features...] + [...professional features...],
    'enterprise': get_all_known_features(),  # everything
}
```

---

## Pricing Display

Show the current plan and renewal date on the Owner Dashboard:

```
╔══════════════════════════════════════╗
║  YOUR PLAN                           ║
║  Professional  ₹2,499/month          ║
║  Renews: 15 Jun 2026                 ║
║  Theme: Luxury                       ║
║  [Upgrade to Enterprise →]           ║
╚══════════════════════════════════════╝
```

The "Upgrade" link goes to a contact/Razorpay flow (out of scope for v1 —
superuser manually upgrades via admin for now).
