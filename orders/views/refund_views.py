# orders/views/refund_views.py
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from core.decorators import tenant_required, role_required
from orders.models import Refund
from orders.services.refund_service import approve_refund, reject_refund

logger = logging.getLogger("pos.orders")

@login_required
@tenant_required
@role_required("owner", "manager")
def pending_refunds_view(request):
    """List all pending refunds for the outlet. Owner can approve/reject inline."""
    refunds = (
        Refund.objects
        .filter(order__tenant=request.user.tenant, order__outlet=request.user.outlet, status="pending")
        .select_related("payment", "order", "refunded_by")
        .order_by("-id")
    )
    return render(request, "orders/pending_refunds.html", {
        "refunds": refunds,
        "is_owner": request.user.role == "owner",
    })


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
        approve_refund(refund_id, request.user, request.user.tenant, request.user.outlet)
        logger.info("User %s approved refund #%s", request.user.username, refund_id)
        return JsonResponse({"success": True, "message": "Refund approved and audit logged"})
    except Exception as e:
        logger.exception("Error approving refund #%s", refund_id)
        return JsonResponse({"error": "Refund could not be approved. Please try again."}, status=400)

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
        reject_refund(refund_id, request.user, request.user.tenant, request.user.outlet, reason=reason)
        logger.info("User %s rejected refund #%s", request.user.username, refund_id)
        return JsonResponse({"success": True, "message": "Refund rejected"})
    except Exception as e:
        logger.exception("Error rejecting refund #%s", refund_id)
        return JsonResponse({"error": "Refund could not be rejected. Please try again."}, status=400)
