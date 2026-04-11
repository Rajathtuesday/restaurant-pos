"""
Authentication and authorization decorators
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect


def role_required(*roles):
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
    Decorator to ensure user has tenant and outlet assigned.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        
        if not request.user.tenant:
            raise PermissionDenied(
                "Your account is not assigned to any restaurant."
            )
        
        if not request.user.outlet:
            raise PermissionDenied(
                "Your account is not assigned to any outlet."
            )
        
        return view_func(request, *args, **kwargs)
    
    return wrapper