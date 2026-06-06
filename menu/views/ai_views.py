"""AI menu importer and multi-outlet menu sync."""
import base64
import logging
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required
from menu.models import MenuCategory, MenuItem

logger = logging.getLogger("pos.menu")


@login_required
@tenant_required
@feature_required("ai_menu_import")
def ai_menu_importer(request):
    """
    Gemini-powered menu parser.
    Queues a Celery task so the Gemini API call never blocks gunicorn.
    Returns {task_id} immediately; frontend polls /menu/ai-import-status/<id>/.
    Falls back to synchronous parsing if Celery is unavailable.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        text      = request.POST.get("text")
        file      = request.FILES.get("file")
        image_b64 = mime_type = None

        if file:
            if file.size > 15 * 1024 * 1024:
                return JsonResponse(
                    {"error": "Image too large — please use a photo under 15 MB."},
                    status=400,
                )
            image_b64 = base64.b64encode(file.read()).decode()
            mime_type = file.content_type

        from menu.tasks import ai_import_menu
        try:
            task = ai_import_menu.delay(
                tenant_id=request.user.tenant_id,
                outlet_id=request.user.outlet_id,
                text=text,
                image_b64=image_b64,
                mime_type=mime_type,
            )
            return JsonResponse({"queued": True, "task_id": task.id})
        except Exception:
            # Celery/Redis down — run synchronously (slower but works)
            logger.warning("Celery unavailable for AI import — running synchronously")
            return _run_sync(request, text, image_b64, mime_type)

    except Exception as e:
        logger.exception("AI Import error")
        return JsonResponse({"error": str(e)}, status=400)


@login_required
@tenant_required
def ai_import_status(request, task_id):
    """Poll endpoint — returns task status stored in cache by ai_import_menu task."""
    result = cache.get(f"ai_import:{task_id}")
    if result is None:
        return JsonResponse({"status": "not_found"}, status=404)
    return JsonResponse(result)


def _run_sync(request, text, image_b64, mime_type):
    """Synchronous fallback used when Celery is unavailable."""
    from core.ai_service import AIService

    image_bytes = base64.b64decode(image_b64) if image_b64 else None
    structured_data = AIService().parse_menu(
        text=text, image_bytes=image_bytes, mime_type=mime_type
    )

    tenant = request.user.tenant
    outlet = request.user.outlet
    _cat_cache: dict = {}

    def _get_or_merge_category(raw_name: str) -> MenuCategory:
        name = (raw_name or "General").strip()
        key  = name.lower()
        if key in _cat_cache:
            return _cat_cache[key]
        cat = MenuCategory.objects.filter(
            tenant=tenant, outlet=outlet, name__iexact=name,
        ).first()
        if not cat:
            cat = MenuCategory.objects.create(
                tenant=tenant, outlet=outlet, name=name,
            )
        _cat_cache[key] = cat
        return cat

    imported_count = 0
    with transaction.atomic():
        for entry in structured_data:
            category = _get_or_merge_category(entry.get("category", "General"))
            for item_data in entry.get("items", []):
                name  = (item_data.get("name") or "").strip()
                price = item_data.get("price", 0)
                if name:
                    MenuItem.objects.get_or_create(
                        tenant=tenant, outlet=outlet,
                        category=category, name=name,
                        defaults={"price": Decimal(str(price))},
                    )
                    imported_count += 1

    return JsonResponse({
        "success": True,
        "message": f"AI imported {imported_count} items.",
    })


@login_required
@tenant_required
@feature_required("multi_outlet")
@require_POST
def sync_menu_to_outlets(request):
    """Push categories, items and recipes from this outlet to all other outlets in the tenant."""
    from tenants.models import Outlet
    from inventory.models import InventoryItem
    from menu.models import Recipe

    if request.user.role != "owner":
        return JsonResponse({"error": "Only owners can sync menus"}, status=403)

    tenant        = request.user.tenant
    source_outlet = request.user.outlet
    target_outlets = Outlet.objects.filter(tenant=tenant).exclude(id=source_outlet.id)

    if not target_outlets.exists():
        return JsonResponse({"error": "No other branches found to sync to."}, status=400)

    source_categories = MenuCategory.objects.filter(tenant=tenant, outlet=source_outlet)
    stats = {
        "categories_created": 0, "items_created": 0,
        "recipes_created": 0, "outlets_updated": target_outlets.count(),
    }

    with transaction.atomic():
        for target_outlet in target_outlets:
            for src_cat in source_categories:
                target_cat, cat_created = MenuCategory.objects.update_or_create(
                    tenant=tenant, outlet=target_outlet, name=src_cat.name,
                    defaults={"is_active": src_cat.is_active},
                )
                if cat_created:
                    stats["categories_created"] += 1

                for src_item in src_cat.items.all():
                    target_item, item_created = MenuItem.objects.update_or_create(
                        tenant=tenant, outlet=target_outlet, name=src_item.name,
                        defaults={
                            "category":            target_cat,
                            "price":               src_item.price,
                            "description":         src_item.description,
                            "estimated_prep_time": src_item.estimated_prep_time,
                            "gst_percentage":      src_item.gst_percentage,
                            "is_available":        src_item.is_available,
                            "available_takeaway":  src_item.available_takeaway,
                            "available_zomato":    src_item.available_zomato,
                            "available_swiggy":    src_item.available_swiggy,
                            "is_veg":              src_item.is_veg,
                        },
                    )
                    if item_created:
                        stats["items_created"] += 1

                    for src_recipe in src_item.recipes.all():
                        target_inv = InventoryItem.objects.filter(
                            tenant=tenant, outlet=target_outlet,
                            name__iexact=src_recipe.inventory_item.name,
                        ).first()
                        if target_inv:
                            _, recipe_created = Recipe.objects.get_or_create(
                                menu_item=target_item, inventory_item=target_inv,
                                defaults={"quantity_required": src_recipe.quantity_required},
                            )
                            if recipe_created:
                                stats["recipes_created"] += 1

    return JsonResponse({"success": True, "stats": stats})
