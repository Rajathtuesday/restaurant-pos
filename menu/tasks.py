import logging
from decimal import Decimal

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache

logger = logging.getLogger("pos.menu")

CACHE_TTL = 600  # 10 minutes — enough for any reasonable import


# The global Celery limit is 30s soft / 60s hard — right for fast tasks like
# printing, but a Gemini PDF/photo call routinely takes longer, especially on a
# small server. Give THIS task its own generous limit (not the global one) so a
# menu import isn't killed mid-call, while other tasks keep the short leash.
@shared_task(bind=True, max_retries=0, soft_time_limit=180, time_limit=200)
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

    except SoftTimeLimitExceeded:
        logger.warning("AI import timed out (soft limit)")
        cache.set(cache_key, {
            "status": "error",
            "error": "The menu took too long to read. Try a smaller or clearer file — "
                     "for a big PDF, split it into a few pages and import them one at a time.",
        }, CACHE_TTL)

    except Exception:
        logger.exception("AI import task failed")
        cache.set(cache_key, {
            "status": "error",
            "error": "Something went wrong while importing this file. Try again, "
                     "or contact support if it keeps happening.",
        }, CACHE_TTL)
