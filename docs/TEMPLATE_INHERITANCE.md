# Template Inheritance Architecture

## Overview

All 40+ standalone HTML templates are being converted to Django template
inheritance using a single `base.html`. This eliminates ~2,000 lines of
duplicated `<head>`, CSS variables, Bootstrap imports, and JavaScript across
every app.

---

## Base Template Location

```
templates/core/base.html   ← single source of truth
```

Every app template replaces its full `<!DOCTYPE html>...` boilerplate with:

```html
{% extends 'core/base.html' %}

{% block title %}Page Title{% endblock %}

{% block extra_css %}
<style>/* page-specific styles only */</style>
{% endblock %}

{% block content %}
<!-- page content -->
{% endblock %}

{% block extra_js %}
<script>/* page-specific JS only */</script>
{% endblock %}
```

---

## Block Regions Defined in base.html

| Block         | Purpose                                              |
|---------------|------------------------------------------------------|
| `title`       | `<title>` tag text                                   |
| `extra_css`   | Page-specific `<style>` or `<link>` tags             |
| `header_left` | Left side of top nav bar (brand / back button)       |
| `header_right`| Right side of top nav bar (actions, logout)          |
| `content`     | Main page body — everything inside `<main>`          |
| `extra_js`    | Page-specific `<script>` blocks at bottom of body    |

---

## What base.html Provides (Do NOT duplicate in child templates)

- `DOCTYPE`, `<html lang="en">`, charset, viewport meta
- Google Fonts (Outfit, DM Serif Display, Space Mono)
- Bootstrap 5.3.3 CSS + Icons
- SweetAlert2 CSS
- PWA manifest link
- CSRF meta tag
- CSS custom properties (`:root` vars) for the active theme
- Dark/light mode toggle logic
- Offline detection banner
- Global progress loader bar
- `themeManager` object
- `ui` object (`toast`, `showLoading`, `hideLoading`, `confirm`)
- `apiClient` object (CSRF-aware fetch wrapper)
- Notification badge poller
- Bootstrap JS, SweetAlert2 JS

---

## Template Inventory and Migration Status

### accounts app

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `login.html`                | No → **Yes**  | Simple form, no header nav   |
| `owner_dashboard.html`      | No → **Yes**  | Heavy page, 4 card blocks    |
| `sales_dashboard.html`      | No → **Yes**  | Reports view                 |
| `feature_flags.html`        | No → **Yes**  | Superuser only               |

### orders app

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `billing.html`              | No → **Yes**  | Fine dining billing          |
| `token_billing.html`        | No → **Yes**  | QSR/Cafe token billing       |
| `token_dashboard.html`      | No → **Yes**  | Token queue view             |
| `kitchen.html`              | No → **Yes**  | Auto-refresh every 10s       |
| `tables.html`               | No → **Yes**  | Floor plan grid              |
| `bill.html`                 | No → **Yes**  | Print bill receipt           |
| `qsr_bill.html`             | No → **Yes**  | QSR thermal receipt          |
| `running_order.html`        | No → **Yes**  | Live order tracker           |
| `waiter_dashboard.html`     | No → **Yes**  | Waiter call view             |
| `order_timeline.html`       | No → **Yes**  | Order event log              |
| `order_locked.html`         | No → **Yes**  | Locked order screen          |

### menu app

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `menu.html`                 | No → **Yes**  | Cashier/waiter menu          |
| `menu_management.html`      | No → **Yes**  | Owner menu editor            |
| `qsr_menu_management.html`  | No → **Yes**  | QSR-specific editor          |
| `modifiers_management.html` | No → **Yes**  | Modifier editor              |
| `gst_management.html`       | No → **Yes**  | GST rates editor             |
| `digital_menu.html`         | No → **Yes**  | Customer-facing QR menu      |

### inventory app

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `inventory_board.html`      | No → **Yes**  | Live stock board             |
| `purchase_orders.html`      | No → **Yes**  | PO list view                 |
| `purchase_order.html`       | No → **Yes**  | Single PO detail             |
| `purchase_order_print.html` | No → **No**   | Print-only, no nav needed    |
| `suppliers.html`            | No → **Yes**  | Supplier list/form           |

### reports app

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `dashboard.html`            | No → **Yes**  | Revenue/sales charts         |
| `kitchen_dashboard.html`    | No → **Yes**  | KOT throughput charts        |

### shifts app

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `shift_list.html`           | No → **Yes**  |                              |
| `shift_detail.html`         | No → **Yes**  |                              |
| `cash_session.html`         | No → **Yes**  |                              |
| `schedule.html`             | No → **Yes**  |                              |

### crm app

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `crm_dashboard.html`        | No → **Yes**  |                              |
| `guest_profile.html`        | No → **Yes**  |                              |
| `reservations.html`         | No → **Yes**  |                              |

### setup app

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `outlet_setup.html`         | No → **Yes**  |                              |
| `menu_setup.html`           | No → **Yes**  |                              |
| `table_setup.html`          | No → **Yes**  |                              |
| All other setup/*.html      | No → **Yes**  |                              |

### Global error pages

| File                        | Extends base? | Notes                        |
|-----------------------------|---------------|------------------------------|
| `templates/400.html`        | No → **Yes**  |                              |
| `templates/403.html`        | No → **Yes**  |                              |
| `templates/404.html`        | No → **Yes**  |                              |
| `templates/500.html`        | No → **Yes**  |                              |

---

## Context Processors Required

The base.html uses `request.user.tenant.*` — add to `settings.py` if not present:

```python
TEMPLATES[0]['OPTIONS']['context_processors'] = [
    ...
    'django.template.context_processors.request',
    'core.context_processors.tenant_context',   # custom — see below
]
```

### core/context_processors.py

```python
def tenant_context(request):
    if not request.user.is_authenticated:
        return {}
    tenant = getattr(request.user, 'tenant', None)
    return {
        'tenant': tenant,
        'tenant_theme': getattr(tenant, 'theme', 'standard'),
        'tenant_features': getattr(tenant, '_feature_overrides', {}),
    }
```

---

## Migration Steps Per Template (Checklist)

For each template file:

1. Replace everything from `<!DOCTYPE html>` to `</head><body>` with:
   `{% extends 'core/base.html' %}`

2. Wrap the `<title>` text in `{% block title %}...{% endblock %}`

3. Move all `<style>` blocks inside `{% block extra_css %}...{% endblock %}`

4. Move all page content (previously inside `<body>`) into
   `{% block content %}...{% endblock %}`

5. Move all page-specific `<script>` blocks into
   `{% block extra_js %}...{% endblock %}`

6. Delete all duplicated: fonts, Bootstrap link, SweetAlert link, CSRF meta,
   `:root` CSS vars, `themeManager`, `ui`, `apiClient`, notification poller.

7. Verify the page renders correctly at its URL.
