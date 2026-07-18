# Theme System

## Concept

Each tenant is assigned a **theme** by the superuser. The theme controls:

- Color palette (background, accent, text)
- Typography (font choices, weights)
- Component style (sharp edges vs rounded, minimal vs rich)
- Border and shadow intensity

The theme is **not** the same as dark/light mode. Dark/light is a user-level
toggle (localStorage). Theme is a tenant-level assignment (database).

---

## Available Themes

## Default Theme

**All tenants get `luxury` by default.** The superuser can change it.
This applies regardless of tenant type (fine_dining, cafe, franchise).

---

### 1. `luxury` — Fine Dining / Premium

Designed for high-end restaurants. Minimal chrome, serif headings, gold
accents, ultra-thin borders. Everything whispers money.

```css
[data-theme="luxury"] {
    --accent-gold:     #c5a059;
    --bg-color:        #fafafa;
    --panel-bg:        #ffffff;
    --text-main:       #111111;
    --text-muted:      #737373;
    --border-color:    #eaeaea;
    --border-radius:   0px;          /* sharp edges */
    --shadow-soft:     0 2px 8px rgba(0,0,0,0.04);
    --font-main:       'Outfit', sans-serif;
    --font-heading:    'DM Serif Display', serif;
    --btn-style:       outlined;     /* btn-luxury class */
}

[data-theme="luxury"].dark {
    --bg-color:        #000000;
    --panel-bg:        #0a0a0a;
    --text-main:       #ffffff;
    --text-muted:      #888888;
    --border-color:    #262626;
}
```

**Visual signature:** Black & white with single gold accent. Uppercase
tracking on labels. DM Serif Display headings. 0px border-radius everywhere.

---

### 2. `standard` — Casual Dining / Multi-purpose

Approachable, clean, works for most restaurants. Rounded corners, friendly
colors, standard card-based layout.

```css
[data-theme="standard"] {
    --accent-gold:     #4f7ef8;      /* blue accent */
    --bg-color:        #f5f7fa;
    --panel-bg:        #ffffff;
    --text-main:       #1a1a2e;
    --text-muted:      #6b7280;
    --border-color:    #e5e7eb;
    --border-radius:   8px;
    --shadow-soft:     0 4px 16px rgba(0,0,0,0.07);
    --font-main:       'Outfit', sans-serif;
    --font-heading:    'Outfit', sans-serif;
    --btn-style:       filled;
}

[data-theme="standard"].dark {
    --bg-color:        #0f172a;
    --panel-bg:        #1e293b;
    --text-main:       #f1f5f9;
    --text-muted:      #94a3b8;
    --border-color:    #334155;
}
```

**Visual signature:** Blue accent, rounded 8px corners, card shadows. Feels
like a modern web app.

---

### 3. `qsr` — Quick Service / Fast Food

Built for speed. High-contrast, large tap targets, bold colors, minimal
decorative elements. Works perfectly on a dusty touch screen behind a counter.

```css
[data-theme="qsr"] {
    --accent-gold:     #ff6b35;      /* orange — urgency, energy */
    --bg-color:        #1a1a1a;      /* dark by default — kitchen lighting */
    --panel-bg:        #262626;
    --text-main:       #ffffff;
    --text-muted:      #a0a0a0;
    --border-color:    #3a3a3a;
    --border-radius:   4px;
    --shadow-soft:     none;         /* flat UI — no distractions */
    --font-main:       'Outfit', sans-serif;
    --font-heading:    'Outfit', sans-serif;
    --btn-style:       bold-filled;
    --min-tap-target:  48px;         /* WCAG 2.5.5 */
}

[data-theme="qsr"].light {
    --bg-color:        #f5f5f5;
    --panel-bg:        #ffffff;
    --text-main:       #111111;
    --text-muted:      #555555;
    --border-color:    #dddddd;
}
```

**Visual signature:** Dark default. Orange accents. Large buttons (min 48px).
Zero shadows. Flat cards. Numbers BIG and readable from 50cm away.

---

### 4. `minimal` — Cafe / Bakery

Light, airy, earthy. Green or terracotta accent. Soft shadows, generous
padding, handcrafted feel without being slow.

```css
[data-theme="minimal"] {
    --accent-gold:     #6b8f71;      /* sage green */
    --bg-color:        #f9f7f4;      /* warm white */
    --panel-bg:        #ffffff;
    --text-main:       #2c2c2c;
    --text-muted:      #888070;
    --border-color:    #e8e2d9;
    --border-radius:   12px;
    --shadow-soft:     0 2px 12px rgba(0,0,0,0.05);
    --font-main:       'Outfit', sans-serif;
    --font-heading:    'DM Serif Display', serif;
    --btn-style:       rounded-outlined;
}

[data-theme="minimal"].dark {
    --bg-color:        #1c1b18;
    --panel-bg:        #252420;
    --text-main:       #f0ece4;
    --text-muted:      #8a8272;
    --border-color:    #383530;
}
```

**Visual signature:** Warm whites, sage green, 12px round corners. Feels like
a specialty coffee shop's app.

---

## How Themes Are Loaded

### 1. Theme is stored on the Tenant model

```python
class Tenant(models.Model):
    THEME_CHOICES = [
        ('luxury',   'Luxury (Fine Dining)'),
        ('standard', 'Standard (Casual)'),
        ('qsr',      'QSR (Quick Service)'),
        ('minimal',  'Minimal (Cafe/Bakery)'),
    ]
    theme = models.CharField(
        max_length=20,
        choices=THEME_CHOICES,
        default='standard'
    )
```

### 2. base.html applies it as a data attribute

```html
<html lang="en" data-theme="{{ tenant.theme|default:'standard' }}">
```

### 3. Theme CSS file is loaded per theme

```html
<link rel="stylesheet"
      href="{% static 'css/themes/' %}{{ tenant.theme|default:'standard' }}.css">
```

### 4. CSS files live in static/

```
static/css/themes/
├── luxury.css
├── standard.css
├── qsr.css
└── minimal.css
```

Each file contains only the `[data-theme="X"]` and `[data-theme="X"].dark`
CSS custom property blocks shown above. No component styles. Just variables.

---

## Theme Inheritance for Dark Mode

Dark mode (`body.dark` toggled by user) is a modifier on top of the theme.
The theme file defines both states:

```css
/* Light state (default) */
[data-theme="luxury"] { --bg-color: #fafafa; }

/* Dark modifier */
[data-theme="luxury"].dark { --bg-color: #000000; }
```

The JS toggle in base.html adds/removes `dark` on `<html>` (not `<body>`)
so the CSS selector `[data-theme="luxury"].dark` matches correctly:

```js
// base.html themeManager — toggle on <html>, not <body>
document.documentElement.classList.toggle('dark');
```

---

## QSR Theme Special Behaviors

The QSR theme also changes component sizing via CSS:

```css
[data-theme="qsr"] .btn {
    min-height: var(--min-tap-target);
    font-size: 1.1rem;
    font-weight: 700;
}

[data-theme="qsr"] .card {
    border-radius: var(--border-radius);
    box-shadow: none;
}

[data-theme="qsr"] input,
[data-theme="qsr"] select {
    min-height: var(--min-tap-target);
    font-size: 1rem;
}
```

This means a QSR tenant's token_billing.html automatically gets large touch
targets just from the theme — no per-page changes needed.

---

## Adding a New Theme

1. Add the choice to `Tenant.THEME_CHOICES`
2. Create `static/css/themes/<name>.css` with the CSS variable block
3. Generate and apply the migration
4. Assign via the superuser Theme Assignment page
