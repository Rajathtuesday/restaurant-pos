# core/context_processors.py
from django.conf import settings

def base_url(request):
    return {'BASE_URL': settings.BASE_URL}

def tenant_features(request):
    # An error page raised before AuthenticationMiddleware (a refused host,
    # say) renders with no request.user at all: answer as for a visitor.
    # Until 27 Nov 2026 this read request.user, and every such 400 became
    # a 500 (core/tests/test_error_pages_early.py).
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'tenant_features': [], 'tenant_type': 'fine_dining'}

    tenant = getattr(user, 'tenant', None)
    if not tenant:
        return {'tenant_features': [], 'tenant_type': 'fine_dining'}

    from core.features import has_feature, get_all_known_features

    # Use the canonical feature list from FEATURE_GROUPS so custom-only features
    # (multi_kitchen, loyalty_points, etc.) are evaluated for tenants that have
    # overrides enabling them: not just the TENANT_FEATURES defaults.
    resolved_features = [f for f in get_all_known_features() if has_feature(tenant, f)]

    return {
        'tenant_features': resolved_features,
        'tenant_type': tenant.tenant_type,
    }



def _own_host(request):
    """The host the page answers on. For a host Rasova refuses (the 400
    page for it renders with this processor too), the main site's: asking
    the request again would raise DisallowedHost inside the error page."""
    from django.core.exceptions import DisallowedHost
    try:
        return request.get_host()
    except DisallowedHost:
        return settings.CANONICAL_HOST


def frontend(request):
    """What every page's scripts need from the server, rendered once by
    base.html as {{ rasova_page|json_script:"rasova-data" }} and read by
    static/js/core/page_data.js. The one way server data reaches a script:
    no template tags inside JavaScript."""
    user = getattr(request, "user", None)
    authenticated = bool(user is not None and user.is_authenticated)
    outlet = getattr(user, "outlet", None) if authenticated else None
    session = getattr(request, "session", {})
    from box.edition import is_box
    box = is_box()
    page = {
        "authenticated": authenticated,
        # The shared /live-demo/ account: no "Install Rasova POS" prompt.
        "demo_session": "demo_trailer_mode" in session,
        # The cloud: always https, because behind Cloudflare request.scheme
        # can say http and the Android print service refuses a cleartext
        # internet address. A box has no https at all (it lives on the
        # restaurant's own network), so it says what it really is.
        "server_url": (request.scheme if box else "https") + "://" + _own_host(request),
        "agent_host": (getattr(outlet, "agent_host", None) or "localhost") if outlet is not None else "localhost",
    }
    if outlet is not None and not box:
        # The outlet's agent key, for the Android app's print service. It
        # travels to the server in a header, never in a URL (printing/views.py).
        # Not on a box: the box prints its own queue (box/print_queue.py), so
        # a phone there has nothing to collect.
        page["agent_key"] = getattr(outlet, "print_agent_key", "") or ""
    # An Android app older than the server needs: base.html shows the
    # "update the app" banner (core/app_version.py).
    from core.app_version import app_update
    return {"rasova_page": page, "app_update": app_update(request),
            "show_setup_checklist": shows_setup_checklist(request.path)}


# Where the "Setup 3/5 complete" checklist is offered: the pages an owner
# sets up from. On a working screen (billing, the bill, the floor plan, the
# kitchen, the token counter) its corner badge covered the screen's own
# buttons (seen on the screenshot sheet, 13 Nov 2026).
SETUP_CHECKLIST_PAGES = ("/dashboard/", "/setup/", "/menu/")


def shows_setup_checklist(path):
    return any(path == p or path.startswith(p) for p in SETUP_CHECKLIST_PAGES) and not path.startswith("/menu/qr")
