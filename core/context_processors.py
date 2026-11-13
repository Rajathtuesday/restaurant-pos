# core/context_processors.py
from django.conf import settings

def base_url(request):
    return {'BASE_URL': settings.BASE_URL}

def tenant_features(request):
    if not request.user.is_authenticated:
        return {'tenant_features': [], 'tenant_type': 'fine_dining'}

    tenant = getattr(request.user, 'tenant', None)
    if not tenant:
        return {'tenant_features': [], 'tenant_type': 'fine_dining'}

    from core.features import has_feature, get_all_known_features

    # Use the canonical feature list from FEATURE_GROUPS so custom-only features
    # (multi_kitchen, loyalty_points, etc.) are evaluated for tenants that have
    # overrides enabling them — not just the TENANT_FEATURES defaults.
    resolved_features = [f for f in get_all_known_features() if has_feature(tenant, f)]

    return {
        'tenant_features': resolved_features,
        'tenant_type': tenant.tenant_type,
    }



def frontend(request):
    """What every page's scripts need from the server, rendered once by
    base.html as {{ rasova_page|json_script:"rasova-data" }} and read by
    static/js/core/page_data.js. The one way server data reaches a script:
    no template tags inside JavaScript."""
    user = getattr(request, "user", None)
    authenticated = bool(user is not None and user.is_authenticated)
    outlet = getattr(user, "outlet", None) if authenticated else None
    session = getattr(request, "session", {})
    page = {
        "authenticated": authenticated,
        # The shared /live-demo/ account: no "Install Rasova POS" prompt.
        "demo_session": "demo_trailer_mode" in session,
        # Always https: behind Cloudflare request.scheme can say http, and the
        # Android print service refuses a cleartext address.
        "server_url": "https://" + request.get_host(),
        "agent_host": (getattr(outlet, "agent_host", None) or "localhost") if outlet is not None else "localhost",
    }
    if outlet is not None:
        # The outlet's agent key, for the Android app's print service. It
        # travels to the server in a header, never in a URL (printing/views.py).
        page["agent_key"] = getattr(outlet, "print_agent_key", "") or ""
    # An Android app older than the server needs: base.html shows the
    # "update the app" banner (core/app_version.py).
    from core.app_version import app_update
    return {"rasova_page": page, "app_update": app_update(request)}
