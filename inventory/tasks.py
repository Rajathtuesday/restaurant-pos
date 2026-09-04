import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.db import transaction

logger = logging.getLogger("pos.inventory")

# rapidfuzz score (0-100) below which a name is sent to the batched Gemini
# match step instead of being trusted as a deterministic fuzzy match.
FUZZY_THRESHOLD = 72

# Confidence (0-1) at/above which a fuzzy match's "include" checkbox
# defaults checked in the review UI. Below this — like every AI-resolved or
# new-ingredient row — it defaults unchecked, forcing explicit review.
FUZZY_HIGH_CONFIDENCE = 0.90


# Own soft/hard limit, same reasoning as menu.tasks.ai_import_menu: the
# global 30s/60s Celery limit is right for fast tasks, wrong for a Gemini
# document read.
@shared_task(bind=True, max_retries=0, soft_time_limit=180, time_limit=200)
def ai_import_recipe(self, job_id, text, image_b64, mime_type):
    """
    Extracts a recipe's ingredients via Gemini, resolves each to an existing
    InventoryItem (rapidfuzz first, one batched Gemini call for the rest),
    and stages the result as RecipeImportLine rows for human review.

    Unlike menu.tasks.ai_import_menu, progress is written to the
    RecipeImportJob row itself, not the Django cache — the job is created
    (status="processing") before .delay() is called, so status polling can
    never hit a "not found" race, and a review can safely outlive any cache
    TTL.

    Nothing here writes to the real Recipe table — that only happens when a
    human confirms via recipe_import_views.recipe_import_confirm, through
    the same inventory.recipe_service.upsert_recipe() the manual "add
    ingredient" UI uses.
    """
    from inventory.models import InventoryItem, RecipeImportJob, RecipeImportLine
    from inventory.recipe_unit_table import parse_quantity_text
    from core.ai_service import AIService
    import base64
    from rapidfuzz import fuzz, process

    try:
        job = RecipeImportJob.objects.select_related("tenant", "outlet").get(pk=job_id)
    except RecipeImportJob.DoesNotExist:
        logger.error("ai_import_recipe: job %s not found", job_id)
        return

    try:
        image_bytes = base64.b64decode(image_b64) if image_b64 else None

        raw_lines = AIService().parse_recipe(text=text, image_bytes=image_bytes, mime_type=mime_type)
        job.raw_extraction = raw_lines
        job.save(update_fields=["raw_extraction"])

        # Malformed/empty entries surface as zero lines, never fabricated ones.
        entries = []
        for raw in raw_lines or []:
            name = (raw.get("ingredient") or "").strip()
            qty_text = (raw.get("quantity_text") or "").strip()
            if name:
                entries.append((name, qty_text))

        inventory_items = list(
            InventoryItem.objects.filter(tenant=job.tenant, outlet=job.outlet)
        )
        inventory_by_name = {item.name.lower(): item for item in inventory_items}
        inventory_names = [item.name for item in inventory_items]

        # Layer 1 — deterministic match: exact name, then rapidfuzz.
        layer1 = {}  # name -> (method, matched_item_or_None, confidence_or_None)
        unmatched_names = []
        for name, _ in entries:
            if name in layer1:
                continue  # duplicate ingredient line — resolve once, reuse
            exact_item = inventory_by_name.get(name.lower())
            if exact_item:
                layer1[name] = ("exact", exact_item, 1.0)
                continue
            best = process.extractOne(name, inventory_names, scorer=fuzz.WRatio) if inventory_names else None
            if best and best[1] >= FUZZY_THRESHOLD:
                layer1[name] = ("fuzzy", inventory_by_name.get(best[0].lower()), best[1] / 100)
            else:
                unmatched_names.append(name)

        # Layer 2 — one batched Gemini call for everything Layer 1 couldn't place.
        ai_matches = AIService().match_ingredients(unmatched_names, inventory_names)

        with transaction.atomic():
            for order, (name, qty_text) in enumerate(entries):
                extracted_qty, extracted_unit, needs_manual = parse_quantity_text(qty_text)

                if name in layer1:
                    method, matched_item, confidence = layer1[name]
                else:
                    ai_name = ai_matches.get(name)
                    matched_item = inventory_by_name.get(ai_name.lower()) if ai_name else None
                    method, confidence = ("ai", None) if matched_item else ("new", None)

                high_confidence = method == "exact" or (
                    method == "fuzzy" and confidence is not None and confidence >= FUZZY_HIGH_CONFIDENCE
                )
                include_default = high_confidence and not needs_manual

                RecipeImportLine.objects.create(
                    job=job, order=order,
                    raw_ingredient_name=name, raw_quantity_text=qty_text,
                    extracted_quantity=extracted_qty, extracted_unit=extracted_unit,
                    needs_manual_quantity=needs_manual,
                    match_method=method, match_confidence=confidence,
                    suggested_inventory_item=matched_item, is_new_ingredient=matched_item is None,
                    # Pre-filled as a convenience default only — nothing is written
                    # to Recipe until a human explicitly confirms the review.
                    resolved_inventory_item=matched_item,
                    final_quantity=extracted_qty, final_unit=extracted_unit,
                    include_in_recipe=include_default,
                )

            job.status = "ready_for_review"
            job.save(update_fields=["status"])

    except SoftTimeLimitExceeded:
        logger.warning("AI recipe import timed out (soft limit) for job %s", job_id)
        job.status = "failed"
        job.error_message = (
            "The recipe document took too long to read. Try a smaller or clearer file."
        )
        job.save(update_fields=["status", "error_message"])

    except Exception:
        logger.exception("AI recipe import task failed for job %s", job_id)
        job.status = "failed"
        job.error_message = (
            "Something went wrong while importing this file. Try again, "
            "or contact support if it keeps happening."
        )
        job.save(update_fields=["status", "error_message"])
