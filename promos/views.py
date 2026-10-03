# promos/views.py
"""
The promo picker on the bill screen reads its list from here. Creating,
switching on and off, and archiving promos live in setup/views/promo_views.py
(the Setup screen). A second, older set of those endpoints used to live in
this file too; it ignored the minimum order, the usage cap and the
all-outlets choice, and was removed on 3 Oct 2026.
"""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse

from core.decorators import tenant_required
from promos.models import Promo


@login_required
@tenant_required
def list_active_promos(request):
    """Promos usable at this outlet right now (outlet-specific and
    tenant-wide), judged by the outlet's business day."""
    outlet = request.user.outlet
    promos = Promo.objects.filter(
        Q(outlet=outlet) | Q(outlet__isnull=True),
        tenant=request.user.tenant,
        is_active=True,
        archived_at__isnull=True,
    ).order_by("-created_at")

    result = [
        {
            "id": p.id,
            "name": p.name,
            "code": p.code,
            "discount_type": p.discount_type,
            "discount_value": str(p.discount_value),
            "min_order": str(p.min_order_value),
        }
        for p in promos if p.is_live_for(outlet)
    ]
    return JsonResponse({"promos": result})
