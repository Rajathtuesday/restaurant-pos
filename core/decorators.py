"""
core/decorators.py
Authentication, authorization, and feature-gate decorators.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect


def role_required(*roles):
    """Restricts a view to users with one of the listed roles.

    Apply after @login_required. The explicit is_authenticated guard means that
    if this is ever stacked without @login_required, an AnonymousUser (which has
    no `.role`) gets a clean 403 instead of an AttributeError 500.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({"error": "Authentication required"}, status=403)
            if request.user.role not in roles and not request.user.is_superuser:
                return JsonResponse({"error": "Permission denied"}, status=403)
            return view_func(request, *args, **kwargs)
        # Kept on the view so a page can ask may() which buttons to show
        # instead of copying the list. Two role_required on one view: both apply.
        inner = getattr(view_func, "allowed_roles", None)
        _wrapped_view.allowed_roles = frozenset(roles) & inner if inner is not None else frozenset(roles)
        return _wrapped_view
    return decorator


def may(user, view):
    """True when `user` gets past `view`'s role_required and
    feature_required, the same checks the view makes. A page uses it to
    decide which buttons to draw, so a button never shows for a request the
    server refuses, and never hides from someone the server lets through."""
    if user.is_superuser:
        return True
    roles = getattr(view, "allowed_roles", None)
    if roles is not None and getattr(user, "role", None) not in roles:
        return False
    from core.features import has_feature
    tenant = getattr(user, "tenant", None)
    return (all(has_feature(tenant, f) for f in getattr(view, "required_features", ()))
            and all(check(tenant) for check in getattr(view, "open_checks", ())))


def open_when(check, refusal):
    """Restricts a view to tenants for which `check(tenant)` is true: a rule
    a list of features cannot say (the Discount/Void Audit is open with
    advanced_reports, and for every pub). may() asks the same check, so a
    link to the view shows exactly where the view opens. Superusers pass,
    as with feature_required."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.user.is_superuser or check(getattr(request.user, "tenant", None)):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied(refusal)
        _wrapped_view.open_checks = tuple(getattr(view_func, "open_checks", ())) + (check,)
        return _wrapped_view
    return decorator


# The session flag that makes base.html say, once, why the page moved
# (core.context_processors.frontend). The session cookie is shared by every
# *.rasova.net address, so the flag set at one restaurant's address is read
# at the other's.
MOVED_FROM_ANOTHER_RESTAURANT = "moved_from_another_restaurant"


def to_own_restaurant(request):
    """A page opened at another restaurant's address by someone signed in to
    their own: a redirect to the same page at their own address, which then
    says why. None when that isn't a person opening a page (a script's
    request, a form post) or there is no own address (on a laptop in DEBUG,
    on a box): the caller refuses those.

    Until 28 Nov 2026 every such page answered a bare 403: one browser holds
    one login for every *.rasova.net address, so an owner who signed in as
    another restaurant from Spice Garden's page met it on Spice's page."""
    if request.method not in ("GET", "HEAD"):
        return None
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return None
    if "text/html" not in request.headers.get("Accept", ""):
        return None
    from accounts.views.auth_views import _subdomain_redirect
    url = _subdomain_redirect(request.user, request.get_full_path())
    if not url:
        return None
    request.session[MOVED_FROM_ANOTHER_RESTAURANT] = True
    return redirect(url)


def tenant_required(view_func):
    """
    Ensures the request user has a tenant and outlet assigned,
    and that any subdomain matches the user's assigned tenant.
    Apply after @login_required.

    Superusers bypass every check below, same as feature_required: a
    platform superuser normally has tenant=None (they aren't scoped to any
    one restaurant), so the "must have a tenant/outlet" and "subdomain must
    match your tenant" checks would otherwise lock them out of any view
    stacked with this decorator, including views that already have their
    own explicit superuser tenant-selection logic (e.g. reports.dashboard's
    ?tenant_id=). Views that need real tenant/outlet data for a superuser
    to actually do anything must resolve that themselves, same as they
    already do today; this decorator's job is only "don't block the
    superuser," not "guess which tenant they mean."
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")

        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        if not request.user.tenant:
            raise PermissionDenied(
                "Your account is not assigned to any restaurant."
            )

        # Cross-tenant isolation: subdomain must match user's tenant. A page
        # opened there goes to the same page at the user's own address, with
        # a note; anything else (a script's request, a form post) is refused.
        if (
            hasattr(request, "tenant")
            and request.tenant
            and request.tenant != request.user.tenant
        ):
            moved = to_own_restaurant(request)
            if moved is not None:
                return moved
            raise PermissionDenied(
                "Cross-tenant access detected. You are logged into a different restaurant."
            )

        if not request.user.outlet:
            raise PermissionDenied(
                "Your account is not assigned to any outlet."
            )

        return view_func(request, *args, **kwargs)

    return wrapper


def feature_required(*features):
    """
    Restricts a view to tenants that have ALL of the listed features
    enabled (as defined in core/features.py → TENANT_FEATURES).

    A franchise tenant cannot access floor_plan views.
    A fine-dining tenant cannot access token_system views.
    Superusers bypass the check (for admin/testing).

    For JSON endpoints: returns 403 JSON.
    For page views: raises PermissionDenied → renders 403.html.

    Usage:
        @login_required
        @tenant_required
        @feature_required("floor_plan")
        def floor_plan_view(request): ...

        @login_required
        @tenant_required
        @feature_required("token_system")
        def token_dashboard(request): ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            # Superusers always pass (needed for admin testing).
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            from core.features import has_feature
            tenant = getattr(request.user, "tenant", None)

            for feature in features:
                if not has_feature(tenant, feature):
                    is_api = (
                        request.path.startswith("/api/")
                        or "application/json" in request.META.get("HTTP_ACCEPT", "")
                    )
                    # In words an owner can act on (templates/403.html shows
                    # them): not an internal feature name, and not "ask your
                    # manager", which the owner was told before 26 Nov 2026.
                    if is_api:
                        return JsonResponse(
                            {"error": "This is not switched on for your restaurant."},
                            status=403,
                        )
                    raise PermissionDenied("This is not switched on for your restaurant.")

            return view_func(request, *args, **kwargs)

        _wrapped_view.required_features = tuple(getattr(view_func, "required_features", ())) + features
        return _wrapped_view
    return decorator