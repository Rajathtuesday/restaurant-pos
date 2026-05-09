"""
core/decorators.py
Authentication, authorization, and feature-gate decorators.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect


def role_required(*roles):
    """Restricts a view to users with one of the listed roles."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if request.user.role not in roles and not request.user.is_superuser:
                return JsonResponse({"error": "Permission denied"}, status=403)
            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


def tenant_required(view_func):
    """
    Ensures the request user has a tenant and outlet assigned,
    and that any subdomain matches the user's assigned tenant.
    Apply after @login_required.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")

        if not request.user.tenant:
            raise PermissionDenied(
                "Your account is not assigned to any restaurant."
            )

        # Cross-tenant isolation: subdomain must match user's tenant.
        if (
            hasattr(request, "tenant")
            and request.tenant
            and request.tenant != request.user.tenant
        ):
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
                    if is_api:
                        return JsonResponse(
                            {
                                "error": (
                                    f"Feature '{feature}' is not available "
                                    "for your account type."
                                )
                            },
                            status=403,
                        )
                    raise PermissionDenied(
                        f"The '{feature}' feature is not available "
                        "for your restaurant type."
                    )

            return view_func(request, *args, **kwargs)

        return _wrapped_view
    return decorator