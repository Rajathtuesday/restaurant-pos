"""AI recipe importer — upload a recipe document, review the AI-suggested
ingredient matches/quantities, and only then write to the real Recipe table.

Every view here is gated behind the ai_recipe_import feature flag (opt-in
per tenant, see core/features.py) and owner/manager role, matching the
existing add_recipe manual-entry gate.
"""
import base64
import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required, role_required
from menu.models import MenuItem
from inventory.models import InventoryItem, Recipe, RecipeImportJob, RecipeImportLine, UNIT_CHOICES
from inventory.recipe_service import RecipeCrossTenantError, RecipeUnitMismatchError, upsert_recipe

logger = logging.getLogger("pos.inventory")


@login_required
@tenant_required
@feature_required("ai_recipe_import")
@role_required("owner", "manager")
@require_POST
def recipe_import_start(request):
    """Create a RecipeImportJob and queue the extraction task (or run it
    synchronously if Celery is unavailable)."""
    menu_item_id = request.POST.get("menu_item")
    text = request.POST.get("text")
    file = request.FILES.get("file")

    if not menu_item_id:
        return JsonResponse({"error": "menu_item is required"}, status=400)
    if not text and not file:
        return JsonResponse({"error": "Upload a file or paste recipe text"}, status=400)

    menu_item = get_object_or_404(
        MenuItem, id=menu_item_id, tenant=request.user.tenant, outlet=request.user.outlet
    )

    image_b64 = mime_type = None
    source_filename = ""
    if file:
        if file.size > 15 * 1024 * 1024:
            return JsonResponse(
                {"error": "File too large — please use a file under 15 MB."}, status=400
            )
        image_b64 = base64.b64encode(file.read()).decode()
        mime_type = file.content_type
        source_filename = file.name

    job = RecipeImportJob.objects.create(
        tenant=request.user.tenant, outlet=request.user.outlet,
        menu_item=menu_item, status="processing",
        source_filename=source_filename, created_by=request.user,
    )

    from inventory.tasks import ai_import_recipe
    try:
        ai_import_recipe.delay(job_id=job.id, text=text, image_b64=image_b64, mime_type=mime_type)
    except Exception:
        logger.warning("Celery unavailable for recipe import — running synchronously")
        ai_import_recipe(job.id, text, image_b64, mime_type)

    return JsonResponse({"success": True, "job_id": job.id})


@login_required
@tenant_required
@feature_required("ai_recipe_import")
def recipe_import_status(request, job_id):
    """Poll endpoint for the upload modal."""
    job = get_object_or_404(
        RecipeImportJob, id=job_id, tenant=request.user.tenant, outlet=request.user.outlet
    )
    return JsonResponse({"status": job.status, "error": job.error_message or None})


@login_required
@tenant_required
@feature_required("ai_recipe_import")
@role_required("owner", "manager")
def recipe_import_review(request, job_id):
    """The mandatory human review screen. Nothing has touched the real
    Recipe table yet — this page is the only path to recipe_import_confirm."""
    job = get_object_or_404(
        RecipeImportJob, id=job_id, tenant=request.user.tenant, outlet=request.user.outlet
    )
    lines = job.lines.select_related("suggested_inventory_item", "resolved_inventory_item")
    inventory_items = InventoryItem.objects.filter(
        tenant=request.user.tenant, outlet=request.user.outlet
    ).order_by("name")
    existing_recipe_count = Recipe.objects.filter(menu_item=job.menu_item).count()

    return render(request, "inventory/recipe_import_review.html", {
        "job": job,
        "lines": lines,
        "inventory_items": inventory_items,
        "existing_recipe_count": existing_recipe_count,
        "unit_choices": UNIT_CHOICES,
    })


@login_required
@tenant_required
@feature_required("ai_recipe_import")
@role_required("owner", "manager")
@require_POST
def recipe_import_update_line(request, line_id):
    """Autosave a single row's reviewer-owned fields as they edit."""
    line = get_object_or_404(
        RecipeImportLine, id=line_id,
        job__tenant=request.user.tenant, job__outlet=request.user.outlet,
    )
    if line.job.status != "ready_for_review":
        return JsonResponse({"error": "This import is no longer editable."}, status=400)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    if "resolved_inventory_item_id" in data:
        inv_id = data["resolved_inventory_item_id"]
        if inv_id:
            # Re-validated server-side, never trusted from the echoed dropdown value.
            line.resolved_inventory_item = get_object_or_404(
                InventoryItem, id=inv_id, tenant=request.user.tenant, outlet=request.user.outlet
            )
        else:
            line.resolved_inventory_item = None

    if "final_quantity" in data:
        raw_qty = data["final_quantity"]
        if raw_qty in (None, ""):
            line.final_quantity = None
        else:
            try:
                qty = Decimal(str(raw_qty))
                if qty <= 0:
                    return JsonResponse({"error": "Quantity must be positive"}, status=400)
                line.final_quantity = qty
            except InvalidOperation:
                return JsonResponse({"error": "Invalid quantity"}, status=400)

    if "final_unit" in data:
        unit = data["final_unit"] or None
        if unit and unit not in dict(InventoryItem._meta.get_field("unit").choices):
            return JsonResponse({"error": "Invalid unit"}, status=400)
        line.final_unit = unit

    if "include_in_recipe" in data:
        line.include_in_recipe = bool(data["include_in_recipe"])

    line.save(update_fields=[
        "resolved_inventory_item", "final_quantity", "final_unit", "include_in_recipe"
    ])
    return JsonResponse({"success": True})


@login_required
@tenant_required
@feature_required("ai_recipe_import")
@role_required("owner", "manager")
@require_POST
def recipe_import_add_line(request, job_id):
    """The review page's "+ Add ingredient" row — covers extraction that
    missed an ingredient entirely, which the AI has nothing to "correct"."""
    job = get_object_or_404(
        RecipeImportJob, id=job_id, tenant=request.user.tenant, outlet=request.user.outlet
    )
    if job.status != "ready_for_review":
        return JsonResponse({"error": "This import is no longer editable."}, status=400)

    next_order = (job.lines.count())
    line = RecipeImportLine.objects.create(
        job=job, order=next_order, match_method="manual", include_in_recipe=False,
    )
    return JsonResponse({"success": True, "line_id": line.id})


@login_required
@tenant_required
@feature_required("ai_recipe_import")
@role_required("owner", "manager")
@require_POST
def recipe_import_confirm(request, job_id):
    """Write every included, fully-resolved line to the real Recipe table —
    all in one transaction so a single bad line can't leave a half-saved
    recipe."""
    job = get_object_or_404(
        RecipeImportJob, id=job_id, tenant=request.user.tenant, outlet=request.user.outlet
    )
    if job.status == "confirmed":
        return JsonResponse({"error": "This recipe import was already confirmed."}, status=400)
    if job.status != "ready_for_review":
        return JsonResponse({"error": "This import isn't ready for review yet."}, status=400)

    included = list(job.lines.filter(include_in_recipe=True).select_related("resolved_inventory_item"))
    incomplete = [
        l.raw_ingredient_name or "(manual row)" for l in included
        if not l.resolved_inventory_item or not l.final_quantity or not l.final_unit
    ]
    if incomplete:
        return JsonResponse({
            "error": "Some included rows are missing an ingredient, quantity, or unit: "
                     + ", ".join(incomplete)
        }, status=400)

    try:
        with transaction.atomic():
            for line in included:
                # Re-fetched fresh (not trusting the staged FK) in case the
                # inventory item was deleted/moved while this job sat in review.
                inv_item = get_object_or_404(
                    InventoryItem, id=line.resolved_inventory_item_id,
                    tenant=request.user.tenant, outlet=request.user.outlet,
                )
                upsert_recipe(job.menu_item, inv_item, line.final_quantity, line.final_unit)

            job.status = "confirmed"
            job.confirmed_at = timezone.now()
            job.save(update_fields=["status", "confirmed_at"])
    except (RecipeUnitMismatchError, RecipeCrossTenantError) as e:
        return JsonResponse({"error": str(e)}, status=400)

    return JsonResponse({"success": True, "count": len(included)})


@login_required
@tenant_required
@feature_required("ai_recipe_import")
@role_required("owner", "manager")
@require_POST
def recipe_import_discard(request, job_id):
    job = get_object_or_404(
        RecipeImportJob, id=job_id, tenant=request.user.tenant, outlet=request.user.outlet
    )
    if job.status == "confirmed":
        return JsonResponse(
            {"error": "Already confirmed — those recipe changes were saved and can't be discarded here."},
            status=400,
        )
    job.status = "discarded"
    job.save(update_fields=["status"])
    return JsonResponse({"success": True})
