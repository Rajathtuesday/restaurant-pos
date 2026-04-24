# Multi-Tenant Subdomain Routing Guide
**POS System — `slug.domain.com/url` pattern**

---

## How It Works

Your `TenantMiddleware` reads the **first subdomain** from the request host and looks up a `Tenant` by its `slug`:

```
pizza-palace.yourdomain.com/billing/
└─── subdomain ──┘
       ↓
Tenant.objects.get(slug="pizza-palace")
       ↓
request.tenant = <Tenant: Pizza Palace>
```

The `slug` is auto-generated from the tenant name when created (e.g. "Pizza Palace" → `pizza-palace`).

---

## The Problem with IP in Development

An IP address like `192.168.1.10:8000` has **no dots** after the port is stripped, so `host.split(".")` gives only `["192", "168", "1", "10"]` — 4 parts, all numeric. The middleware logic requires `len(parts) > 2` **and** a non-numeric first part to treat it as a subdomain, so `request.tenant` is always `None` on bare IPs.

```python
# Current middleware — breaks on plain IP
parts = host.split(".")   # ["192","168","1","10"]
if len(parts) > 2:        # ← True but parts[0] is "192", not a slug
    subdomain = parts[0]
    Tenant.objects.get(slug="192")  # ← DoesNotExist every time
```

---

## Solution A — `?tenant=slug` Query-Param Fallback (Recommended for Dev)

Update `TenantMiddleware` to also accept a query-param when no real subdomain is present. Zero configuration on the client — just append `?tenant=pizza-palace` to any URL during development.

```python
# core/middleware.py
from tenants.models import Tenant


class TenantMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]
        parts = host.split(".")

        tenant = None

        # --- Primary: real subdomain (production path) ---
        if len(parts) > 2 and not parts[0].replace("-", "").isdigit():
            subdomain = parts[0]
            try:
                tenant = Tenant.objects.get(slug=subdomain, is_active=True)
            except Tenant.DoesNotExist:
                tenant = None

        # --- Fallback: ?tenant=slug query param (dev/IP path) ---
        if tenant is None:
            slug = request.GET.get("tenant") or request.session.get("dev_tenant_slug")
            if slug:
                try:
                    tenant = Tenant.objects.get(slug=slug, is_active=True)
                    # Persist in session so you don't need to pass it on every request
                    request.session["dev_tenant_slug"] = slug
                except Tenant.DoesNotExist:
                    tenant = None

        request.tenant = tenant
        response = self.get_response(request)
        return response
```

### Usage in dev

```
# First request — sets the session cookie
http://192.168.1.10:8000/billing/?tenant=pizza-palace

# All subsequent requests in the same browser session work without the param
http://192.168.1.10:8000/billing/
http://192.168.1.10:8000/pay/123/
```

> [!NOTE]
> The session fallback means you only need `?tenant=slug` once per browser session. After that, every tab on the same origin picks it up automatically.

---

## Solution B — `hosts` File Fake Subdomains (Closest to Production)

Edit your Windows `hosts` file so fake subdomains resolve to your machine. No code changes needed.

**File:** `C:\Windows\System32\drivers\etc\hosts`

```
# POS Dev tenants
127.0.0.1   pizza-palace.pos.local
127.0.0.1   spice-garden.pos.local
127.0.0.1   burger-barn.pos.local
```

Then update `settings.py`:

```python
# In .env or settings.py
ALLOWED_HOSTS = ["*.pos.local", "localhost", "127.0.0.1"]
```

And access the app at:

```
http://pizza-palace.pos.local:8000/billing/
http://spice-garden.pos.local:8000/kitchen/
```

> [!IMPORTANT]
> Django's dev server binds to `localhost` by default. Run it with `--host` to bind to the right interface:
> ```bash
> python manage.py runserver 0.0.0.0:8000
> ```
> You also need wildcard `ALLOWED_HOSTS` support. Use `django-allow-cidr` or just list each hostname explicitly during dev.

---

## Solution C — Nginx Reverse Proxy (Production-ready locally)

Install Nginx and proxy subdomains to Django. This is what you'll use when you have a real domain.

```nginx
# /etc/nginx/sites-available/pos
server {
    listen 80;
    server_name *.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;          # ← critical: passes subdomain to Django
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

With your IP + a wildcard DNS (e.g. `*.192.168.1.10.nip.io`):

```
http://pizza-palace.192.168.1.10.nip.io/billing/
```

[nip.io](https://nip.io) is a free public wildcard DNS service — `*.192.168.1.10.nip.io` resolves to `192.168.1.10` automatically, no configuration needed.

---

## Recommended Dev Setup (Quick Start)

| Step | Action |
|------|--------|
| 1 | Apply **Solution A** middleware changes above |
| 2 | Run server: `python manage.py runserver 0.0.0.0:8000` |
| 3 | Add `ALLOWED_HOSTS = ["*"]` in `settings.py` when `DEBUG=True` (already guarded) |
| 4 | Open `http://192.168.1.10:8000/billing/?tenant=pizza-palace` |
| 5 | All subsequent URLs work without the query param (session holds it) |

---

## Production Checklist (When You Have a Domain)

```
yourdomain.com      → Agency/marketing landing page
slug.yourdomain.com → Tenant POS dashboard
```

```python
# settings.py — production
ALLOWED_HOSTS = [".yourdomain.com"]   # leading dot = wildcard for all subdomains
```

```
DNS A record:     yourdomain.com     → server IP
DNS A record:     *.yourdomain.com   → server IP   ← wildcard subdomain
```

The `TenantMiddleware` then handles the rest automatically from the subdomain.

---

## Current Middleware Limitation to Note

The existing middleware does **not** return a 404 when `request.tenant is None`. Any view that calls `request.tenant` without checking will throw `AttributeError`. Your `@tenant_required` decorator already guards this — make sure every tenant-scoped view uses it.
