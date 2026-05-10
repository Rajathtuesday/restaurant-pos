# orders/views/refund_views.py
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from core.decorators import tenant_required, role_required
from orders.services.refund_service import approve_refund, reject_refund

logger = logging.getLogger("pos.orders")

@login_required
@tenant_required
@require_POST
@role_required("owner")
@ratelimit(key="user", rate="5/m", method="POST", block=True)
def approve_refund_view(request, refund_id):
    """
    Approves a pending refund. Owner only.
    Rate-limited to 5/min per user to prevent a compromised account
    from bulk-approving fraudulent refund requests.
    """
    try:
        approve_refund(refund_id, request.user)
        logger.info(f"User {request.user.username} approved refund #{refund_id}")
        return JsonResponse({"success": True, "message": "Refund approved and audit logged"})
    except Exception as e:
        logger.error(f"Error approving refund #{refund_id}: {e}")
        return JsonResponse({"error": str(e)}, status=400)

@login_required
@tenant_required
@require_POST
@role_required("manager", "owner")
@ratelimit(key="user", rate="5/m", method="POST", block=True)
def reject_refund_view(request, refund_id):
    """
    Rejects a pending refund. Manager or Owner.
    Rate-limited to 5/min per user.
    """
    try:
        data = json.loads(request.body)
        reason = data.get("reason", "")
        reject_refund(refund_id, request.user, reason=reason)
        logger.info(f"User {request.user.username} rejected refund #{refund_id}")
        return JsonResponse({"success": True, "message": "Refund rejected"})
    except Exception as e:
        logger.error(f"Error rejecting refund #{refund_id}: {e}")
        return JsonResponse({"error": str(e)}, status=400)
