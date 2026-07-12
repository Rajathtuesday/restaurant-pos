# setup/views/promo_views.py
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core.decorators import tenant_required


# ==================================
# PROMO / DISCOUNT MANAGEMENT
# ==================================

@login_required
@tenant_required
def setup_promos(request):
    """
    Full-page promo management UI inside the Setup area.
    Accessible by owner and manager.
    """
    from django.shortcuts import render, redirect
    if request.user.role not in ["owner", "manager"]:
        return redirect("/setup/")

    from orders.models import Promo
    from tenants.models import Outlet

    tenant = request.user.tenant
    outlet = request.user.outlet

    # All outlets for this tenant (used by the "All Outlets" toggle)
    all_outlets = Outlet.objects.filter(tenant=tenant).order_by("name")

    # Promos scoped to this tenant (includes all-outlet ones + this outlet's)
    promos = Promo.objects.filter(tenant=tenant).select_related("outlet").order_by("-created_at")

    return render(request, "setup/setup_promos.html", {
        "promos": promos,
        "outlets": all_outlets,
        "current_outlet": outlet,
    })


@login_required
@tenant_required
@require_POST
def promo_create(request):
    """JSON endpoint — create a new promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from orders.models import Promo
    from tenants.models import Outlet
    from decimal import Decimal, InvalidOperation
    from django.db import IntegrityError

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name           = data.get("name", "").strip()
    code           = data.get("code", "").strip().upper()
    description    = data.get("description", "").strip()
    discount_type  = data.get("discount_type", "percentage")
    all_outlets_flag = data.get("all_outlets", False)

    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)
    if discount_type not in ("percentage", "amount"):
        return JsonResponse({"error": "Invalid discount type"}, status=400)

    try:
        discount_value  = Decimal(str(data.get("discount_value", "0")))
        min_order_value = Decimal(str(data.get("min_order_value", "0")))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid numeric value"}, status=400)

    if discount_value <= 0:
        return JsonResponse({"error": "Discount value must be positive"}, status=400)
    if discount_type == "percentage" and discount_value > 100:
        return JsonResponse({"error": "Percentage cannot exceed 100"}, status=400)

    max_uses    = data.get("max_uses") or None
    valid_from  = data.get("valid_from") or None
    valid_until = data.get("valid_until") or None

    # Resolve outlet — None = all outlets
    outlet = None if all_outlets_flag else request.user.outlet

    # Validate outlet_id override (owner-only: pick a specific outlet)
    outlet_id = data.get("outlet_id")
    if outlet_id and not all_outlets_flag:
        try:
            outlet = Outlet.objects.get(id=outlet_id, tenant=request.user.tenant)
        except Outlet.DoesNotExist:
            return JsonResponse({"error": "Outlet not found"}, status=404)

    try:
        promo = Promo.objects.create(
            tenant          = request.user.tenant,
            outlet          = outlet,
            name            = name,
            code            = code,
            description     = description,
            discount_type   = discount_type,
            discount_value  = discount_value,
            min_order_value = min_order_value,
            max_uses        = int(max_uses) if max_uses else None,
            valid_from      = valid_from or None,
            valid_until     = valid_until or None,
        )
    except IntegrityError as e:
        return JsonResponse({"error": f"Database error: {str(e)}"}, status=409)
    except Exception as e:
        return JsonResponse({"error": f"Unexpected error: {str(e)}"}, status=500)

    return JsonResponse({
        "success": True,
        "id":      promo.id,
        "name":    promo.name,
        "scope":   promo.outlet.name if promo.outlet_id else "All Outlets",
    })


@login_required
@tenant_required
@require_POST
def promo_toggle(request, promo_id):
    """Toggle is_active on a promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from orders.models import Promo
    try:
        promo = Promo.objects.get(id=promo_id, tenant=request.user.tenant)
    except Promo.DoesNotExist:
        return JsonResponse({"error": "Promo not found"}, status=404)

    promo.is_active = not promo.is_active
    promo.save(update_fields=["is_active"])
    return JsonResponse({"success": True, "is_active": promo.is_active})


@login_required
@tenant_required
@require_POST
def promo_delete(request, promo_id):
    """Hard-delete a promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from orders.models import Promo
    try:
        promo = Promo.objects.get(id=promo_id, tenant=request.user.tenant)
    except Promo.DoesNotExist:
        return JsonResponse({"error": "Promo not found"}, status=404)

    promo.delete()
    return JsonResponse({"success": True})
