"""GST rate management: per-item and bulk per-category."""
import json
import logging
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from core.decorators import tenant_required
from menu.models import MenuCategory, MenuItem

logger = logging.getLogger("pos.menu")

_VALID_GST = [Decimal("0"), Decimal("5"), Decimal("12"), Decimal("18"), Decimal("28")]
_GST_RATES = [
    {"value": "0.00",  "label": "0% — Exempt"},
    {"value": "5.00",  "label": "5% — Non-AC Restaurant"},
    {"value": "12.00", "label": "12% — Packaged Food"},
    {"value": "18.00", "label": "18% — AC / Liquor License"},
    {"value": "28.00", "label": "28% — 5-Star / Premium (rare)"},
]


@login_required
@tenant_required
def gst_management(request):
    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()
    categories = MenuCategory.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet, is_active=True
    ).prefetch_related("items")
    return render(request, "menu/gst_management.html", {
        "categories": categories, "gst_rates": _GST_RATES,
    })


@login_required
@tenant_required
@require_POST
def update_item_gst(request, item_id):
    if request.user.role not in ["owner", "manager"]:
        return JsonResponse({"error": "Permission denied"}, status=403)
    item = get_object_or_404(
        MenuItem, id=item_id, tenant=request.user.tenant, outlet=request.user.outlet
    )
    try:
        data = json.loads(request.body)
        gst  = Decimal(str(data.get("gst_percentage", "5.00")))
        if gst not in _VALID_GST:
            return JsonResponse({"error": "Invalid GST rate"}, status=400)
        item.gst_percentage = gst
        item.save(update_fields=["gst_percentage"])
        logger.info("User %s set GST for '%s' → %s%%", request.user.username, item.name, gst)
        return JsonResponse({
            "success": True, "item_id": item.id,
            "gst_percentage": str(gst), "message": f"{item.name} GST updated to {gst}%",
        })
    except Exception:
        logger.exception("Error updating item GST")
        return JsonResponse({"error": "Could not update GST. Please try again."}, status=400)


@login_required
@tenant_required
@require_POST
def update_category_gst(request, category_id):
    if request.user.role not in ["owner", "manager"]:
        return JsonResponse({"error": "Permission denied"}, status=403)
    category = get_object_or_404(
        MenuCategory, id=category_id, tenant=request.user.tenant, outlet=request.user.outlet
    )
    try:
        data    = json.loads(request.body)
        gst     = Decimal(str(data.get("gst_percentage", "5.00")))
        if gst not in _VALID_GST:
            return JsonResponse({"error": "Invalid GST rate"}, status=400)
        updated = category.items.filter(
            tenant=request.user.tenant, outlet=request.user.outlet
        ).update(gst_percentage=gst)
        logger.info(
            "User %s bulk-set GST for category '%s' → %s%% (%s items)",
            request.user.username, category.name, gst, updated,
        )
        return JsonResponse({
            "success": True, "category_id": category.id,
            "gst_percentage": str(gst), "updated_count": updated,
            "message": f"{updated} items in {category.name} updated to {gst}%",
        })
    except Exception:
        logger.exception("Error bulk-updating category GST")
        return JsonResponse({"error": "Could not update GST. Please try again."}, status=400)
