# setup/views/promo_views.py
import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core.decorators import tenant_required

logger = logging.getLogger("pos.setup")


class PromoInputError(Exception):
    """A promo form value the owner has to correct; the message says how."""


def read_promo_fields(data):
    """The promo form, checked: the Promo fields to save, or PromoInputError.

    Everything a typo could get wrong is refused here with a message, instead
    of being saved (an end date before the start, a negative minimum, a cap
    of 0 that the model reads as unlimited) or turning into a 500.
    """
    from datetime import date
    from decimal import Decimal, InvalidOperation

    name = str(data.get("name") or "").strip()
    if not name:
        raise PromoInputError("Name is required.")
    if len(name) > 120:
        raise PromoInputError("The name can be at most 120 characters.")
    code = str(data.get("code") or "").strip().upper()
    if len(code) > 30 or (code and not code.replace("-", "").replace("_", "").isalnum()):
        raise PromoInputError("The code can be up to 30 letters, numbers, - or _.")
    discount_type = data.get("discount_type", "percentage")
    if discount_type not in ("percentage", "amount"):
        raise PromoInputError("Invalid discount type.")

    def money(key, label):
        raw = data.get(key)
        if raw in (None, ""):
            return Decimal("0")
        try:
            value = Decimal(str(raw))
        except InvalidOperation:
            raise PromoInputError(f"{label} must be a number.")
        if not value.is_finite() or value < 0:
            raise PromoInputError(f"{label} can't be negative.")
        if value != value.quantize(Decimal("0.01")):
            raise PromoInputError(f"{label} can have at most 2 decimal places.")
        return value

    discount_value = money("discount_value", "The discount")
    if discount_value <= 0:
        raise PromoInputError("The discount must be more than 0.")
    if discount_type == "percentage" and discount_value > 100:
        raise PromoInputError("A percentage can't be more than 100.")
    if discount_value >= Decimal("1000000"):
        raise PromoInputError("The discount is too large.")
    min_order_value = money("min_order_value", "The minimum order")

    max_uses = data.get("max_uses")
    if max_uses in (None, ""):
        max_uses = None
    else:
        if isinstance(max_uses, bool) or not str(max_uses).strip().isdigit():
            raise PromoInputError("The usage cap must be a whole number, or blank for no cap.")
        max_uses = int(str(max_uses).strip())
        if max_uses < 1:
            raise PromoInputError("The usage cap must be at least 1, or blank for no cap.")

    def day(key, label):
        raw = data.get(key)
        if raw in (None, ""):
            return None
        try:
            return date.fromisoformat(str(raw))
        except ValueError:
            raise PromoInputError(f"{label} must be a date like 2026-10-31.")

    valid_from = day("valid_from", "The start date")
    valid_until = day("valid_until", "The end date")
    if valid_from and valid_until and valid_until < valid_from:
        raise PromoInputError("The end date is before the start date.")

    return {
        "name": name, "code": code, "description": str(data.get("description") or "").strip()[:2000],
        "discount_type": discount_type, "discount_value": discount_value,
        "min_order_value": min_order_value, "max_uses": max_uses,
        "valid_from": valid_from, "valid_until": valid_until,
    }


# ==================================
# PROMO / DISCOUNT MANAGEMENT
# ==================================

@login_required
@tenant_required
def setup_promos(request):
    """
    Full-page promo management UI inside the Setup area.
    Accessible by owner and manager.
    """
    from django.shortcuts import render, redirect
    if request.user.role not in ["owner", "manager"]:
        return redirect("/setup/")

    from django.db.models import Q
    from promos.models import Promo
    from tenants.models import Outlet

    tenant = request.user.tenant
    outlet = request.user.outlet

    # All outlets for this tenant (used by the "All Outlets" toggle)
    all_outlets = Outlet.objects.filter(tenant=tenant).order_by("name")

    # Promos scoped to this tenant (includes all-outlet ones + this outlet's) —
    # NOT every outlet's promos, matching the page heading below.
    promos = Promo.objects.filter(tenant=tenant, archived_at__isnull=True).filter(
        Q(outlet=outlet) | Q(outlet__isnull=True)
    ).select_related("outlet").order_by("-created_at")

    return render(request, "setup/setup_promos.html", {
        "promos": promos,
        "outlets": all_outlets,
        "current_outlet": outlet,
    })


@login_required
@tenant_required
@require_POST
def promo_create(request):
    """JSON endpoint — create a new promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from promos.models import Promo
    from tenants.models import Outlet
    from django.db import IntegrityError

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        fields = read_promo_fields(data)
    except PromoInputError as e:
        return JsonResponse({"error": str(e)}, status=400)
    all_outlets_flag = data.get("all_outlets", False) is True

    # Resolve outlet — None = all outlets
    outlet = None if all_outlets_flag else request.user.outlet

    # Validate outlet_id override (owner-only: pick a specific outlet)
    outlet_id = data.get("outlet_id")
    if outlet_id and not all_outlets_flag:
        try:
            outlet = Outlet.objects.get(id=outlet_id, tenant=request.user.tenant)
        except Outlet.DoesNotExist:
            return JsonResponse({"error": "Outlet not found"}, status=404)

    try:
        promo = Promo.objects.create(tenant=request.user.tenant, outlet=outlet, **fields)
    except IntegrityError:
        # The only constraint a valid form can hit: the code is unique per tenant.
        return JsonResponse({"error": f"The code {fields['code']} is already used by another promo."}, status=409)
    except Exception:
        logger.exception("Unexpected error creating promo")
        return JsonResponse({"error": "Could not create the promo. Please try again."}, status=500)

    return JsonResponse({
        "success": True,
        "id":      promo.id,
        "name":    promo.name,
        "scope":   promo.outlet.name if promo.outlet_id else "All Outlets",
    })


@login_required
@tenant_required
@require_POST
def promo_toggle(request, promo_id):
    """Toggle is_active on a promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from promos.models import Promo
    try:
        promo = Promo.objects.get(id=promo_id, tenant=request.user.tenant)
    except Promo.DoesNotExist:
        return JsonResponse({"error": "Promo not found"}, status=404)

    # A promo scoped to one specific outlet is off-limits to a manager at a
    # different outlet — only tenant-wide promos (outlet=None) and this
    # outlet's own promos are toggleable here. Owners keep cross-outlet
    # access by design, same as everywhere else in setup/.
    if request.user.role != "owner" and promo.outlet_id and promo.outlet_id != request.user.outlet_id:
        return JsonResponse({"error": "Promo not found"}, status=404)

    promo.is_active = not promo.is_active
    promo.save(update_fields=["is_active"])
    return JsonResponse({"success": True, "is_active": promo.is_active})


@login_required
@tenant_required
@require_POST
def promo_delete(request, promo_id):
    """Hard-delete a promo."""
    if request.user.role not in ["owner", "manager"]:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from promos.models import Promo
    try:
        promo = Promo.objects.get(id=promo_id, tenant=request.user.tenant)
    except Promo.DoesNotExist:
        return JsonResponse({"error": "Promo not found"}, status=404)

    # Same outlet boundary as promo_toggle above — a manager may not delete
    # another outlet's promo just because they share a tenant. Owners keep
    # cross-outlet access by design.
    if request.user.role != "owner" and promo.outlet_id and promo.outlet_id != request.user.outlet_id:
        return JsonResponse({"error": "Promo not found"}, status=404)

    # Archived, not deleted: bills that used the promo keep pointing at it.
    # Its code is free again (the unique rule skips archived promos).
    from django.utils import timezone
    promo.archived_at = timezone.now()
    promo.is_active = False
    promo.save(update_fields=["archived_at", "is_active"])
    return JsonResponse({"success": True})
