# tablemerge/views.py
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required
from core.errors import error_response

logger = logging.getLogger("pos.orders")


@login_required
@tenant_required
@feature_required("merge_tables")
@require_POST
def merge_tables_view(request):
    from tablemerge.services import MergeError, MergeRefused, merge_tables
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
    except MergeRefused as e:
        return error_response(e, 400)
    except MergeError as e:
        return error_response(e, 404)
    logger.info("User %s merged tables", request.user.username)
    return JsonResponse({"success": True, "merge_id": merge.id})


@login_required
@tenant_required
@feature_required("merge_tables")
@require_POST
def unmerge_tables_view(request, table_id):
    # Any table of the merge, main or joined: every merge connected to it
    # ends (tablemerge.services.unmerge_around).
    from tablemerge.services import unmerge_around
    if not unmerge_around(request.user, table_id):
        return JsonResponse({"error": "Merge not found"}, status=404)
    logger.info("User %s unmerged the tables around %s", request.user.username, table_id)
    return JsonResponse({"success": True})
