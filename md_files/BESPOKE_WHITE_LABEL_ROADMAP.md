# 🎨 Bespoke White-Label Roadmap: The Petpooja Killer
**Project:** Premium Fine Dining POS
**Strategy:** Win by making the restaurant owner feel like they own a custom-built software, not a generic tool.

---

## 1. Phase 1: The "Theme Engine" (Database Driven)
Currently, your colors are hardcoded in CSS. We need to move them to the Database so each restaurant looks different.

### The `TenantTheme` Model:
```python
class TenantTheme(models.Model):
    tenant = models.OneToOneField('tenants.Tenant', on_delete=models.CASCADE)
    
    # Colors
    primary_color = models.CharField(max_length=7, default="#c5a059")  # Gold
    bg_color = models.CharField(max_length=7, default="#fafafa")       # Light Gray
    accent_color = models.CharField(max_length=7, default="#111111")   # Dark
    
    # Typography
    font_family = models.CharField(max_length=100, default="'Outfit', sans-serif")
    heading_font = models.CharField(max_length=100, default="'DM Serif Display', serif")
    
    # Custom Branding
    custom_css = models.TextField(blank=True, null=True)
    hide_platform_branding = models.BooleanField(default=False) # The "Premium" Toggle
```

---

## 2. Phase 2: Dynamic CSS Injection
Instead of editing 20 HTML files, we use **CSS Variables** in your `base.html`.

### The Implementation:
```html
<style>
:root {
    --accent-gold: {{ theme.primary_color }};
    --bg-color: {{ theme.bg_color }};
    --text-main: {{ theme.accent_color }};
    --font-main: {{ theme.font_family }};
    --font-heading: {{ theme.heading_font }};
}
</style>
```
*Now, if the owner changes a color in the settings, the entire Digital Menu and POS change instantly.*

---

## 3. Phase 3: The "Luxury Presets"
Restaurant owners are busy. They don't want to pick hex codes. Give them **Presets**:
1.  **"The Ritz"**: Deep Navy & Gold.
2.  **"Organic"**: Sage Green & Cream.
3.  **"Noir"**: True Black & Sharp White (High Contrast).
4.  **"Heritage"**: Burgundy & Parchment.

---

## 4. Phase 4: White-Label POS (The "Hidden" Software)
When a waiter uses the iPad, the owner wants to see **THEIR** logo, not yours.
*   **Action:** Replace all instances of "POS System" with `{{ tenant.name }}`.
*   **Action:** Allow custom Login screens for each restaurant.

---

## 5. Phase 5: Custom Domains (The Ultimate Moat)
Petpooja URLs look like `petpooja.com/order/...`.
Your URLs should look like `menu.rasova.in` or `order.thesteakhouse.com`.

### Technical Path:
- Use **Django Tenant Middleware** to detect the hostname.
- Point CNAME records to your server.
- Automatically provision SSL certificates via Let's Encrypt (Caddy or Nginx).

---

## 🏁 Why this wins 10% of the market:
7,500 restaurants in the "Fine Dining" and "Boutique Cafe" segment are tired of looking like a McDonald's kiosk. They spend millions on their interior design—they will pay you to make their **Digital Interior** (their software) match their vibe.

**Should we start by building the `TenantTheme` model and connecting it to your Digital Menu?**
