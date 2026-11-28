import json
import re
import time
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render, resolve_url
from tenants.models import Tenant
from . import offline_copy
from .public_pages import PUBLIC_PATHS
from .utils import visitor_used_https
from .tenant_context import set_current_tenant_outlet, clear_current_tenant_outlet
from .request_context import set_current_trace_id, clear_current_trace_id

request_logger = logging.getLogger("pos.core")


class CloudflareSchemeMiddleware:
    """
    Tell Django when the visitor is really on https.

    Django reads the scheme from X-Forwarded-Proto (SECURE_PROXY_SSL_HEADER),
    but nginx sets that to its own scheme, and Cloudflare reaches nginx over
    plain http. This sets it from Cloudflare's CF-Visitor header, believed only
    from Cloudflare (core.utils.visitor_used_https). Must run first: Django's
    SecurityMiddleware reads the scheme (HSTS), and every view after it
    builds absolute links from it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if visitor_used_https(request):
            request.META["HTTP_X_FORWARDED_PROTO"] = "https"
        return self.get_response(request)


class SearchIndexingMiddleware:
    """
    Decides which pages search engines may index. Sits before WhiteNoise,
    because WhiteNoise serves the marketing pages (public/) on every host,
    so spice.rasova.net/compare/ (and any made-up subdomain) was a copy of
    rasova.net/compare/, and Google indexed the copy instead of the real
    one. It had also indexed rasova.net/login/, since nothing said not to.

    Indexing is opt-in: only the marketing pages on the main site and the
    guest menu on a restaurant's own subdomain are indexable. Every other
    HTML page gets "X-Robots-Tag: noindex". The header (not robots.txt) is
    the right tool here: a Disallow in robots.txt stops Google reading the
    page, so it never sees the noindex and can keep the URL indexed.

    On subdomains, the marketing pages other than / move to the main site with
    a 301, and www moves entirely. The home page stays on subdomains (staff
    open their own subdomain to reach login), it is only marked noindex.
    """

    MARKETING_PATHS = PUBLIC_PATHS
    MARKETING_ONLY_PATHS = PUBLIC_PATHS - {"/"}
    GUEST_MENU_PREFIXES = ("/menu/digital-menu/", "/menu/qr/")

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def indexable(cls, host, path):
        main = settings.CANONICAL_HOST
        if host == main:
            return path in cls.MARKETING_PATHS
        if host.endswith("." + main):
            return path.startswith(cls.GUEST_MENU_PREFIXES)
        return False

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower()
        main = settings.CANONICAL_HOST

        if request.method in ("GET", "HEAD") and host.endswith("." + main):
            if host == "www." + main or request.path in self.MARKETING_ONLY_PATHS:
                from django.http import HttpResponsePermanentRedirect
                return HttpResponsePermanentRedirect(f"https://{main}{request.get_full_path()}")

        response = self.get_response(request)
        if (response.get("Content-Type", "").startswith("text/html")
                and "X-Robots-Tag" not in response
                and not self.indexable(host, request.path)):
            response["X-Robots-Tag"] = "noindex"
        return response


class TenantMiddleware:
    """
    Resolves request.tenant from the hostname, for host/branding/routing
    purposes only. Deliberately does NOT touch the tenant query-scoping
    context (see core/tenant_context.py) -- a session cookie is valid
    across every *.rasova.net subdomain (SESSION_COOKIE_DOMAIN), and the
    superuser/portal panel is reachable on any of them, not host-restricted.
    Scoping queries off the subdomain-resolved tenant would make a
    superuser managing tenant B while physically on tenant A's subdomain
    silently require both at once -- an impossible condition that returns
    an empty queryset, not an error. ContextLoggingMiddleware, which runs
    after authentication and scopes off request.user.tenant instead, is
    the only thing that sets that context now.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]
        parts = host.split(".")

        tenant = None

        # --- Primary: real subdomain (production path) ---
        if len(parts) > 2 and not parts[0].replace("-", "").isdigit():
            subdomain = parts[0].lower()   # slugs are always lowercase
            try:
                tenant = Tenant.objects.get(slug__iexact=subdomain, is_active=True)
            except Tenant.DoesNotExist:
                tenant = None

        # --- Fallback: ?tenant=slug query param or session (dev / IP access) ---
        if tenant is None and settings.DEBUG:
            slug = request.GET.get("tenant") or request.session.get("dev_tenant_slug")
            if slug:
                try:
                    tenant = Tenant.objects.get(slug=slug, is_active=True)
                    request.session["dev_tenant_slug"] = slug
                except Tenant.DoesNotExist:
                    tenant = None

        request.tenant = tenant

        response = self.get_response(request)
        return response


class SubscriptionStatusMiddleware:
    """
    Blocks access for tenants whose subscription_status is 'suspended'.
    Must run after AuthenticationMiddleware (needs request.user) and after
    TenantMiddleware (needs request.tenant).

    Only gates already-authenticated, non-superuser requests. Anonymous
    requests pass through untouched: this is deliberate, not an oversight:
    gating before login too would also block a superuser from reaching the
    login page on a suspended tenant's subdomain to investigate/support it,
    since superuser status can't be known until after they've logged in.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = getattr(request, "tenant", None)
        if (
            tenant
            and tenant.subscription_status == "suspended"
            and request.user.is_authenticated
            and not request.user.is_superuser
            and request.path != "/logout/"
        ):
            return render(request, "tenants/suspended.html", {"tenant": tenant}, status=402)
        return self.get_response(request)


class CurrentRequestMiddleware:
    """The request being answered, for orders/services/paid_guard.py: when a
    change takes a bill below what was paid, it needs who is making it and
    the manager's PIN that came with it, deep inside Order.recalculate_totals
    where no view hands them over. Set for the request, then cleared."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from orders.services.paid_guard import current_request
        token = current_request.set(request)
        try:
            return self.get_response(request)
        finally:
            current_request.reset(token)


class ContextLoggingMiddleware:
    """
    The single setter and clearer of core.tenant_context, which
    TenantManager reads to auto-scope every TenantScopedModel query (see
    core/models.py). Runs after authentication and scopes off
    request.user.tenant/request.user.outlet, NOT request.tenant -- see
    the docstring on TenantMiddleware above for why that distinction is
    load-bearing, not stylistic. Superuser accounts have tenant=None, so
    this naturally, correctly leaves them unscoped with no special-casing.

    Wrapped in try/finally so the context can never survive past the
    request that set it, even if the view raises -- this is the one
    thing that MUST hold for TenantManager's auto-filtering to be safe
    rather than a source of cross-request data leakage on a reused
    worker thread.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # trace_id set first, deliberately -- it must not depend on auth
        # having resolved (anonymous QR-menu requests need one too).
        # Honors an inbound X-Trace-Id so this stays correct if a load
        # balancer or API gateway ever supplies its own upstream.
        import uuid
        trace_id = request.headers.get('X-Trace-Id') or str(uuid.uuid4())
        set_current_trace_id(trace_id)

        tenant_id = None
        outlet_id = None
        if request.user.is_authenticated:
            if getattr(request.user, 'tenant', None):
                tenant_id = request.user.tenant_id
            if getattr(request.user, 'outlet', None):
                outlet_id = request.user.outlet_id

        set_current_tenant_outlet(tenant_id, outlet_id)

        try:
            response = self.get_response(request)
        finally:
            # Clear context after request finishes to prevent leak between threads
            clear_current_tenant_outlet()
            clear_current_trace_id()

        response['X-Trace-Id'] = trace_id
        return response


class NoStoreForAuthenticatedResponsesMiddleware:
    """
    Stops a browser from showing a stale authenticated page via Back/Forward
    cache after logout. logout_view() already destroys the session correctly
    server-side -- any real new request already redirects to login -- but a
    small number of views (dashboard, tokens, billing_core) were the only ones
    telling the browser not to keep a navigable snapshot at all (@never_cache).
    Every other authenticated page had no such header, so pressing Back after
    logout could show the last-rendered page without ever recontacting the
    server. Applies Cache-Control: no-store to every authenticated response,
    once, centrally -- new views get this for free instead of needing to
    remember @never_cache individually.

    The service worker keeps app pages for offline whatever Cache-Control
    says (offline billing needs them). An admin page carries
    X-Rasova-Offline: never, and the service worker keeps nothing that says
    so: it never has to know the admin's address, which is a secret in
    production (ADMIN_URL, 27 Nov 2026).
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.user.is_authenticated:
            response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        if request.path.startswith("/" + settings.ADMIN_URL):
            response[offline_copy.HEADER] = offline_copy.NEVER
        return response


SIGNED_OUT = "You're signed out. Sign in again to carry on."


def from_a_script(request):
    """True when a page's script sent the request, not the browser opening
    a page. Browsers say so themselves in Sec-Fetch-Dest: "empty" for
    fetch() and XMLHttpRequest, "document" or "iframe" for a page.
    X-Requested-With, or an Accept of JSON without HTML, covers a client
    that sends no Sec-Fetch headers."""
    headers = request.headers
    if headers.get("Sec-Fetch-Dest") == "empty":
        return True
    if headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = headers.get("Accept", "")
    return "application/json" in accept and "text/html" not in accept


class SignedOutScriptMiddleware:
    """
    A page's script that asks after its session ended is told so: 401 and
    {"error": ..., "signed_out": true}, not a redirect to the sign-in page.

    Why (28 Nov 2026): one sign-in covers every tab and every restaurant
    address in a browser (SESSION_COOKIE_DOMAIN), so signing out in one tab
    signs out the kitchen screen in another. Its scripts followed the
    redirect and got the sign-in page instead of data: the notification
    badge and the dashboard numbers said nothing and went on asking once a
    minute for hours, and the boards showed "Please sign in again." where
    the tickets were. A 401 is an answer every script can read: the page
    shows one notice (static/js/core/signed_out.js) and the live screens
    stop asking (static/js/core/live_feed.js).

    Only a signed-out visitor sent to the sign-in page is answered this
    way: a signed-in person turned away by a check is not signed out, and
    the browser opening a page still gets the sign-in page. Last in
    MIDDLEWARE, under RequestLoggingMiddleware, so the log line shows the
    401 that was sent.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (response.status_code in (301, 302, 303, 307, 308)
                and not getattr(getattr(request, "user", None), "is_authenticated", False)
                and urlsplit(response.get("Location", "")).path == urlsplit(resolve_url(settings.LOGIN_URL)).path
                and from_a_script(request)):
            answer = JsonResponse({"error": SIGNED_OUT, "signed_out": True}, status=401)
            answer.signed_out = True        # RequestLoggingMiddleware: an ordinary answer, not a warning
            return answer
        return response


# The words a refusal gave, for the log line: one line, at most this long.
_REASON_MAX = 200
_NOT_ONE_LINE = re.compile(r"[\x00-\x1f\x7f\s]+")


def refusal_words(response):
    """What a 4xx JSON answer said to the person ({"error": ...}, or
    "message" or "detail"), on one line, or None. Without it a log line
    said "POST /pay/89564/ 400" and the reason had to be worked out from
    the answer's size in bytes (28 Nov 2026). Control characters and line
    breaks become spaces, so words that carry what someone typed cannot
    start a line of their own in the log."""
    if not 400 <= response.status_code < 500 or response.status_code == 404:
        return None
    if getattr(response, "streaming", False) or "json" not in response.get("Content-Type", ""):
        return None
    if len(response.content) > 4096:
        return None
    try:
        data = json.loads(response.content)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    words = next((data[k] for k in ("error", "message", "detail") if isinstance(data.get(k), str) and data[k].strip()), None)
    if words is None:
        return None
    words = _NOT_ONE_LINE.sub(" ", words).strip()
    return words if len(words) <= _REASON_MAX else words[:_REASON_MAX - 1] + "…"


class RequestLoggingMiddleware:
    """
    Logs every HTTP request: method, path, status, duration, user, tenant.
    Add after ContextLoggingMiddleware in MIDDLEWARE so user/tenant are already set.
    """

    # Paths not worth logging (health checks, static assets)
    _SKIP_PREFIXES = ("/static/", "/media/", "/health/", "/favicon.")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if any(request.path.startswith(p) for p in self._SKIP_PREFIXES):
            return self.get_response(request)

        t0 = time.monotonic()
        response = self.get_response(request)
        duration_ms = round((time.monotonic() - t0) * 1000)

        user = request.user
        username = user.username if user.is_authenticated else "anon"
        tenant_name = getattr(getattr(user, "tenant", None), "name", None)
        if not tenant_name:
            tenant_name = getattr(getattr(request, "tenant", None), "name", "–")

        status = response.status_code
        # The "you're signed out" 401 replaced a redirect to the sign-in page
        # (SignedOutScriptMiddleware) and is as ordinary as the redirect was,
        # so not a warning. Any other 401 (a webhook's bad signature) is.
        ordinary = getattr(response, "signed_out", False)
        level = logging.WARNING if status >= 400 and not ordinary else logging.INFO
        reason = refusal_words(response)

        request_logger.log(
            level,
            "%s %s %s  %dms  user=%s  tenant=%s" + ("  reason=%s" if reason else ""),
            request.method,
            request.path,
            status,
            duration_ms,
            username,
            tenant_name,
            *((json.dumps(reason, ensure_ascii=False),) if reason else ()),
        )

        return response