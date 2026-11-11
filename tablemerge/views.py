# tablemerge/views.py
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required
from tablemerge.models import TableMerge

logger = logging.getLogger("pos.orders")


@login_required
@tenant_required
@feature_required("merge_tables")
@require_POST
def merge_tables_view(request):
    from tablemerge.services import MergeError, merge_tables
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "Invalid request body"}, status=400)
    try:
        primary_id = int(data.get("primary_table"))
        table_ids = [int(t) for t in (data.get("tables") or [])]
    except (TypeError, ValueError, AttributeError):
        return JsonResponse({"error": "primary_table and tables are required"}, status=400)
    if not table_ids:
        return JsonResponse({"error": "Pick at least one table to merge"}, status=400)
    try:
        merge = merge_tables(request.user, primary_id, table_ids)
    except MergeError as e:
        return JsonResponse({"error": str(e)}, status=404)
    logger.info("User %s merged tables", request.user.username)
    return JsonResponse({"success": True, "merge_id": merge.id})


@login_required
@tenant_required
@feature_required("merge_tables")
@require_POST
def unmerge_tables_view(request, primary_id):
    from tablemerge.services import unmerge_tables
    merge = TableMerge.objects.filter(
        primary_table_id=primary_id, tenant=request.user.tenant,
        outlet=request.user.outlet, is_active=True
    ).first()
    if not merge:
        return JsonResponse({"error": "Merge not found"}, status=404)
    unmerge_tables(request.user, merge.id)
    logger.info("User %s unmerged table group %s", request.user.username, primary_id)
    return JsonResponse({"success": True})
