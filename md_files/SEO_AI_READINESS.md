# 🤖 AI & Search Readiness Guide
**Project:** Fine Dining POS (Digital Menu)
**Purpose:** Making your menu crawlable, discoverable, and understandable by Search Engines (Google) and AI (Gemini, ChatGPT).

---

## 1. Why do you need this?
When you share your digital menu link on WhatsApp, or when someone searches "Best Pasta in [Your City]" on Google, you want:
1. A beautiful preview image and description.
2. AI to understand that your website is a **Restaurant Menu** and not just a random webpage.
3. Your items and prices to appear directly in Google search results.

---

## 2. The "Must-Have" Meta Tags
These go in the `<head>` section of your `digital_menu.html`.

### A. Open Graph (For WhatsApp, Social Media, AI Previews)
```html
<!-- Primary Meta Tags -->
<title>{{ tenant.name }} | Premium Digital Menu</title>
<meta name="title" content="{{ tenant.name }} | Digital Menu">
<meta name="description" content="Explore our curated selection of gourmet dishes. Order online from your table at {{ tenant.name }}.">

<!-- Open Graph / Facebook / AI Summarizers -->
<meta property="og:type" content="website">
<meta property="og:url" content="{{ request.build_absolute_uri }}">
<meta property="og:title" content="{{ tenant.name }} Digital Menu">
<meta property="og:description" content="Experience bespoke dining. View our menu and order instantly.">
<meta property="og:image" content="{{ tenant.logo.url }}">
```

### B. Robots Control
To allow AI crawlers (like Gemini or GPTBot) to read your menu:
```html
<meta name="robots" content="index, follow">
```

---

## 3. The "AI Secret Weapon": Schema.org (JSON-LD)
This is the most powerful tool. It's a block of hidden code that tells an AI exactly what the page is about in a structured format.

You should add this to your `digital_menu.html`:

```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Restaurant",
  "name": "{{ tenant.name }}",
  "image": "{{ tenant.logo.url }}",
  "@id": "{{ request.build_absolute_uri }}",
  "url": "{{ request.build_absolute_uri }}",
  "telephone": "{{ outlet.phone }}",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "{{ outlet.address }}",
    "addressLocality": "{{ outlet.city }}",
    "addressCountry": "IN"
  },
  "menu": "{{ request.build_absolute_uri }}",
  "servesCuisine": "Multi-cuisine",
  "priceRange": "₹₹"
}
</script>
```

---

## 4. Robots.txt (The Front Door)
Create a file named `robots.txt` in your `static` or `templates` root:
```text
User-agent: *
Allow: /menu/digital-menu/
Disallow: /admin/
Disallow: /billing/
Disallow: /kitchen/

Sitemap: https://yourdomain.com/sitemap.xml
```

---

## 🏁 Action Plan
1. **Update `digital_menu.html`**: I will add the Open Graph and Schema.org tags for you.
2. **Create `robots.txt`**: This ensures crawlers stay away from your private POS data (orders/billing) but index your beautiful menus.

**Would you like me to inject these SEO/AI tags into your `digital_menu.html` now?**
