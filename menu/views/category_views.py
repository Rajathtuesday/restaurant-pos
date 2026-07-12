"""Category CRUD."""
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, role_required
from menu.models import MenuCategory

logger = logging.getLogger("pos.menu")


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def create_category(request):
    try:
        data = json.loads(request.body)
        name = data.get("name")
        if not name:
            return JsonResponse({"error": "Category name required"}, status=400)
        MenuCategory.objects.create(
            tenant=request.user.tenant, outlet=request.user.outlet, name=name
        )
        logger.info("User %s created category '%s'", request.user.username, name)
        return JsonResponse({"success": True})
    except Exception as e:
        logger.error("Error creating category: %s", e)
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@tenant_required
@role_required("owner", "manager")
@require_POST
def delete_category(request, category_id):
    try:
        category = get_object_or_404(
            MenuCategory, id=category_id,
            tenant=request.user.tenant, outlet=request.user.outlet
        )
        name = category.name
        category.delete()
        logger.warning("User %s deleted category '%s' and all its items", request.user.username, name)
        return JsonResponse({"success": True})
    except Exception as e:
        logger.error("Error deleting category: %s", e)
        return JsonResponse({"error": str(e)}, status=500)
