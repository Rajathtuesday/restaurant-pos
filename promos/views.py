# promos/views.py
import json
import logging
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils.timezone import localdate

from core.decorators import tenant_required, role_required
from promos.models import Promo

logger = logging.getLogger("pos.promos")


@login_required
@tenant_required
def list_active_promos(request):
    """
    Returns currently-valid promos for the current outlet so bill.html
    can populate the promo picker dropdown via fetch().
    Includes outlet-specific and tenant-wide (all-outlet) promos.
    """
    from django.db.models import Q
    from decimal import Decimal

    # Promos that are either for this outlet OR all outlets
    promos = Promo.objects.filter(
        Q(outlet=request.user.outlet) | Q(outlet__isnull=True),
        tenant=request.user.tenant,
        is_active=True
    ).order_by("-created_at")

    result = []
    for p in promos:
        if p.is_currently_valid:
            result.append({
                "id": p.id,
                "name": p.name,
                "code": p.code,
                "discount_type": p.discount_type,
                "discount_value": str(p.discount_value),
                "min_order": str(p.min_order_value),
            })
    return JsonResponse({"promos": result})


@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
def create_promo(request):
    """Create a new promo for this outlet."""
    try:
        data = json.loads(request.body)
        name = data.get("name", "").strip()
        code = data.get("code", "").strip().upper()
        discount_type = data.get("discount_type", "percentage")
        discount_value = data.get("discount_value", 0)
        valid_from = data.get("valid_from") or None
        valid_until = data.get("valid_until") or None

        if not name:
            return JsonResponse({"error": "Promo name is required"}, status=400)
        if discount_type not in ["percentage", "amount"]:
            return JsonResponse({"error": "Invalid discount type"}, status=400)

        from decimal import Decimal, InvalidOperation
        try:
            discount_value = Decimal(str(discount_value))
        except InvalidOperation:
            return JsonResponse({"error": "Invalid discount value"}, status=400)

        if discount_value <= 0:
            return JsonResponse({"error": "Discount value must be greater than 0"}, status=400)
        if discount_type == "percentage" and discount_value > 100:
            return JsonResponse({"error": "Percentage cannot exceed 100"}, status=400)

        promo = Promo.objects.create(
            tenant=request.user.tenant,
            outlet=request.user.outlet,
            name=name,
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            valid_from=valid_from,
            valid_until=valid_until,
            is_active=True,
        )
        return JsonResponse({"success": True, "id": promo.id, "name": str(promo)})

    except Exception:
        logger.exception("Error creating promo")
        return JsonResponse({"error": "Could not create the promo. Please try again."}, status=500)


@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
def toggle_promo(request, promo_id):
    """Activate / deactivate a promo."""
    try:
        promo = Promo.objects.get(
            id=promo_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        promo.is_active = not promo.is_active
        promo.save(update_fields=["is_active"])
        return JsonResponse({"success": True, "is_active": promo.is_active})
    except Promo.DoesNotExist:
        return JsonResponse({"error": "Promo not found"}, status=404)


@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
def delete_promo(request, promo_id):
    """Hard-delete a promo."""
    try:
        promo = Promo.objects.get(
            id=promo_id,
            tenant=request.user.tenant,
            outlet=request.user.outlet
        )
        promo.delete()
        return JsonResponse({"success": True})
    except Promo.DoesNotExist:
        return JsonResponse({"error": "Promo not found"}, status=404)
