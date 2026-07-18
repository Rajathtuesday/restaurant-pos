# UI Overhaul Implementation Plan

## Yes, This Is a UI Overhaul

Template inheritance + theme system + mobile-first responsive redesign touches
every screen in the product. It is the largest single change since initial
launch. Estimated: 3-4 weeks of focused work.

---

## Guiding Principles

### For anyone — 10th pass student, 60-year-old uncle, or a 10-year-old kid

1. **One action per screen.** Don't show 10 things when the user needs to do 1.
2. **Buttons are huge.** Minimum 48px tap target on every touchable element.
3. **Words, not icons.** If an icon doesn't have a label, add one.
4. **Color means something.** Green = good. Red = stop/delete. Orange = wait.
   Never use color alone to convey meaning — add text too.
5. **No hidden menus.** Nothing should require discovery. If it exists, show it.
6. **Confirmation before destruction.** Every delete/void/cancel shows a
   plain-language modal: "Are you sure you want to cancel Token #42?"
7. **Mobile first.** Design for the phone first. Desktop is a bonus.

---

## Mobile-First Responsive Rules

All templates must work on:
- Mobile: 360px – 480px (primary for kitchen staff, waiters, cashiers)
- Tablet: 768px – 1024px (primary for billing, token dashboard)
- Desktop: 1280px+ (primary for reports, menu management, owner dashboard)

### CSS Breakpoint Strategy

```css
/* Write for mobile first, then scale UP */

/* Base = mobile (360px+) */
.card-grid { display: flex; flex-direction: column; gap: 1rem; }

/* Tablet (768px+) */
@media (min-width: 768px) {
    .card-grid { flex-direction: row; flex-wrap: wrap; }
    .card-grid > * { flex: 1 1 calc(50% - 0.5rem); }
}

/* Desktop (1200px+) */
@media (min-width: 1200px) {
    .card-grid > * { flex: 1 1 calc(33.33% - 0.67rem); }
}
```

### Navigation: Mobile Bottom Bar

On screens < 768px, show a bottom navigation bar instead of a top nav.
Staff don't look up — they look down at their hand.

```
┌─────────────────────────────────────────────┐
│                  (content)                  │
│                                             │
├──────────┬──────────┬──────────┬────────────┤
│  📋 Menu │  🎫 Token│  🔔 Calls│  ☰ More   │
└──────────┴──────────┴──────────┴────────────┘
```

The `{% block bottom_nav %}` block in base.html renders this on mobile.
On desktop, it's hidden (`d-none d-md-none`). The top header is shown instead.

---

## Phase 1 — Foundation (Week 1)

**Goal:** base.html works, theme CSS files exist, 3 core templates converted.

### Tasks

- [ ] Add `subscription_tier` and `theme` fields to `Tenant` model
- [ ] Generate and apply migration
- [ ] Create `static/css/themes/` directory with 4 CSS files
- [ ] Update `base.html`:
  - Apply `data-theme` attribute on `<html>`
  - Load theme CSS via `<link>` tag
  - Add `{% block bottom_nav %}` for mobile nav
  - Fix themeManager to toggle on `<html>` not `<body>`
- [ ] Add `tenant_context` context processor, register in settings
- [ ] Convert `owner_dashboard.html` → extends base
- [ ] Convert `billing.html` → extends base
- [ ] Convert `token_billing.html` → extends base
- [ ] Test all 3 on mobile (Chrome DevTools, iPhone SE size)

---

## Phase 2 — Kitchen, Orders, Tables (Week 1–2)

**Goal:** Operations staff screens converted and mobile-friendly.

### Screens

| Screen              | Primary Device | Key Mobile Fix                        |
|---------------------|----------------|---------------------------------------|
| `kitchen.html`      | Tablet (fixed) | Large KOT cards, tap to update        |
| `tables.html`       | Tablet         | Grid → single-column on small screen  |
| `token_dashboard.html` | Tablet/Phone| Token number BIG (3rem+)              |
| `waiter_dashboard.html` | Phone      | Card list, bottom nav                 |
| `running_order.html` | Phone/Tablet  | Status pills, swipeable               |
| `billing.html`      | Tablet         | Cart on bottom sheet on mobile        |

### Mobile-specific changes for billing.html

On mobile, the menu grid and cart are tabs/sheets, not side-by-side:

```
Mobile (< 768px):
┌─────────────────────┐
│  [Menu] [Cart (3)] ← tabs
├─────────────────────┤
│                     │
│   Menu Items Grid   │  (or cart if Cart tab active)
│   (2 columns)       │
│                     │
├─────────────────────┤
│  [Charge ₹450  →]   │  ← sticky bottom bar
└─────────────────────┘

Desktop (> 768px):
┌────────────┬────────┐
│ Menu Grid  │  Cart  │
│            │        │
│            │[Charge]│
└────────────┴────────┘
```

---

## Phase 3 — Management Screens (Week 2–3)

**Goal:** Owner, manager, and staff management screens converted.

### Screens

| Screen                  | Primary Device | Notes                          |
|-------------------------|----------------|--------------------------------|
| `menu_management.html`  | Desktop/Tablet | Drag & drop on tablet          |
| `inventory_board.html`  | Desktop/Tablet | Table → card view on mobile    |
| `purchase_orders.html`  | Desktop        | Table with horizontal scroll   |
| `reports/dashboard.html`| Desktop        | Charts collapse to cards       |
| `crm_dashboard.html`    | Desktop/Tablet |                                |
| `shifts/*.html`         | Desktop        |                                |

### Mobile table strategy

Wide data tables should get horizontal scroll on small screens:

```css
.table-responsive-mobile {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
}

@media (max-width: 767px) {
    /* Card view alternative for key tables */
    .table-as-cards thead { display: none; }
    .table-as-cards tr { display: block; margin-bottom: 1rem; padding: 1rem;
                         border: 1px solid var(--border-color);
                         border-radius: var(--border-radius); }
    .table-as-cards td { display: flex; justify-content: space-between; }
    .table-as-cards td::before {
        content: attr(data-label);
        font-weight: 600;
        color: var(--text-muted);
    }
}
```

---

## Phase 4 — Setup, Auth, Superuser (Week 3)

**Goal:** All remaining templates converted.

### Screens

- `setup/*.html` — Onboarding wizard (mobile-friendly step-by-step)
- `login.html` — Full-screen centered card, works on any size
- `feature_flags.html` → `superuser_tenant_settings.html`
- `digital_menu.html` — Customer-facing QR menu (phone only — highest priority)
- Error pages (400, 403, 404, 500)

### Login page mobile layout

```
┌─────────────────────┐
│                     │
│       LOGO          │
│    Rasova POS       │
│                     │
│  ┌───────────────┐  │
│  │  username     │  │
│  └───────────────┘  │
│  ┌───────────────┐  │
│  │  password     │  │
│  └───────────────┘  │
│                     │
│  [ LOGIN  →  ]      │
│                     │
└─────────────────────┘
```

Full viewport height, vertically centered, works perfectly on a phone.

---

## Phase 5 — Theme Polish & QA (Week 4)

**Goal:** All 4 themes look correct across all converted screens.

### QA Checklist Per Screen

- [ ] Renders correctly on 360px phone (Chrome DevTools)
- [ ] Renders correctly on 768px tablet
- [ ] Renders correctly on 1440px desktop
- [ ] Dark mode works for all 4 themes
- [ ] `luxury` theme: zero border-radius, gold accent, serif headings
- [ ] `qsr` theme: min 48px buttons, dark default, orange accent
- [ ] `standard` theme: blue accent, 8px radius
- [ ] `minimal` theme: warm white, sage green, 12px radius
- [ ] No hardcoded colors (all use CSS custom properties)
- [ ] No duplicated `<head>` content remains in any child template
- [ ] `{% extends 'core/base.html' %}` is line 1 of every converted template

---

## File Structure After Overhaul

```
static/
  css/
    themes/
      luxury.css       ← CSS custom property overrides only
      standard.css
      qsr.css
      minimal.css

templates/
  core/
    base.html          ← single source of truth for all HTML structure
  accounts/
    login.html         ← {% extends 'core/base.html' %}
    owner_dashboard.html
    superuser_tenant_settings.html   ← NEW
    superuser_tenant_list.html       ← NEW
  orders/
    billing.html
    token_billing.html
    ... (all others)
  menu/
    menu.html
    ... (all others)
  ... etc
```

---

## What Does NOT Change

- URL structure
- View logic
- Django models
- API endpoints
- Feature gating (`has_feature()`)
- Test suite

The overhaul is **purely presentational** — all behaviour stays identical.

---

## Risk: Breaking a Page During Conversion

The safest migration process per template:

1. Open the page in browser → screenshot before
2. Convert → screenshot after
3. Check: font, colors, layout, all interactive elements
4. Check on mobile (DevTools)
5. Run the test suite after each batch

If a template breaks, the fix is always in the `{% block %}` structure — the
view and URL haven't changed.
