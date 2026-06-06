import logging
from decimal import Decimal

from celery import shared_task
from django.core.cache import cache

logger = logging.getLogger("pos.menu")

CACHE_TTL = 600  # 10 minutes — enough for any reasonable import


@shared_task(bind=True, max_retries=0)
def ai_import_menu(self, tenant_id, outlet_id, text, image_b64, mime_type):
    """
    Runs Gemini menu parsing in a Celery worker so gunicorn workers are free.
    Result stored in cache under key ai_import:<task_id>.
    """
    task_id = self.request.id
    cache_key = f"ai_import:{task_id}"

    cache.set(cache_key, {"status": "processing"}, CACHE_TTL)

    try:
        from tenants.models import Tenant, Outlet
        from menu.models import MenuCategory, MenuItem
        from core.ai_service import AIService
        import base64

        tenant = Tenant.objects.get(pk=tenant_id)
        outlet = Outlet.objects.get(pk=outlet_id)

        image_bytes = base64.b64decode(image_b64) if image_b64 else None

        structured_data = AIService().parse_menu(
            text=text, image_bytes=image_bytes, mime_type=mime_type
        )

        imported_count = 0
        _cat_cache = {}

        def _get_or_merge_category(raw_name):
            name = (raw_name or "General").strip()
            key = name.lower()
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

        from django.db import transaction
        with transaction.atomic():
            for entry in structured_data:
                category = _get_or_merge_category(entry.get("category", "General"))
                for item_data in entry.get("items", []):
                    name = (item_data.get("name") or "").strip()
                    price = item_data.get("price", 0)
                    if name:
                        MenuItem.objects.get_or_create(
                            tenant=tenant, outlet=outlet,
                            category=category, name=name,
                            defaults={"price": Decimal(str(price))},
                        )
                        imported_count += 1

        cache.set(cache_key, {
            "status": "done",
            "message": f"AI imported {imported_count} items.",
            "count": imported_count,
        }, CACHE_TTL)

    except Exception as e:
        logger.exception("AI import task failed")
        cache.set(cache_key, {"status": "error", "error": str(e)}, CACHE_TTL)
