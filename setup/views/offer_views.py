# setup/views/offer_views.py
"""
Setup, then Offers: the owner or a manager sets up offers that apply
themselves (offers/engine.py): buy N get M free, happy hour, % or Rs off,
for chosen dishes or categories, on chosen days and times. A live example
bill on the page shows what a guest would pay before saving.

Every number and choice is checked here before anything is saved
(read_offer_fields); the model's own rules (Offer.clean) are the backstop.
"""
from core.errors import UserError, error_response
import json
import logging
from datetime import date, time

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import feature_required, tenant_required
from core.validators import NumberInputError, read_number

logger = logging.getLogger("pos.setup")

MANAGERS = ("owner", "manager")
TEMPLATES = ("buy_get_free", "happy_hour", "percent_off", "amount_off")


class OfferInputError(UserError, ValueError):
    """Something on the form to fix; str(e) says what, for the screen."""


def _whole(raw, label, low, high):
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise OfferInputError(f"{label} must be a whole number.")
    if not low <= value <= high:
        raise OfferInputError(f"{label} must be from {low} to {high}.")
    return value


def _time(raw, label):
    if raw in (None, ""):
        return None
    try:
        hours, minutes = str(raw).split(":")[:2]
        return time(int(hours), int(minutes))
    except (TypeError, ValueError):
        raise OfferInputError(f"{label} must be a time like 17:00.")


def _date(raw, label):
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise OfferInputError(f"{label} must be a date.")


def _ids(raw, label):
    if raw in (None, ""):
        return []
    if not isinstance(raw, list) or any(isinstance(x, bool) or not str(x).isdigit() for x in raw):
        raise OfferInputError(f"{label}: pick from the list.")
    return sorted({int(x) for x in raw})


def read_offer_fields(data, outlet):
    """The offer, its targets and its window from the form, all checked.
    `outlet` is the outlet the offer is for (None: every outlet)."""
    from menu.models import MenuCategory, MenuItem
    from offers.models import Offer

    if not isinstance(data, dict):
        raise OfferInputError("Nothing to save.")
    template = data.get("template")
    if template not in TEMPLATES:
        raise OfferInputError("Pick what kind of offer this is.")

    name = " ".join(str(data.get("name") or "").split())
    if not name:
        raise OfferInputError("Give the offer a name, as the bill will show it.")
    if len(name) > 80:
        raise OfferInputError("The name can be at most 80 characters.")

    fields = {"name": name, "buy_qty": None, "free_qty": 1, "percent": None, "amount": None}
    try:
        if template == "buy_get_free":
            fields["kind"] = "buy_get_free"
            fields["buy_qty"] = _whole(data.get("buy_qty"), "Buy", 1, 19)
            fields["free_qty"] = _whole(data.get("free_qty"), "Free", 1, 19)
            if fields["buy_qty"] + fields["free_qty"] > 20:
                raise OfferInputError("Buy and free together can be at most 20.")
        elif template in ("happy_hour", "percent_off"):
            fields["kind"] = "percent_off"
            fields["percent"] = read_number(data.get("percent"), "The percent off", maximum=100,
                                            field=Offer._meta.get_field("percent"))
            if fields["percent"] <= 0:
                raise OfferInputError("The percent off must be more than 0.")
        else:
            fields["kind"] = "amount_off"
            fields["amount"] = read_number(data.get("amount"), "The amount off",
                                           field=Offer._meta.get_field("amount"))
            if fields["amount"] <= 0:
                raise OfferInputError("The amount off must be more than 0.")
    except NumberInputError as e:
        raise OfferInputError(str(e))

    fields["priority"] = _whole(data.get("priority") or 0, "Priority", -100, 100)
    fields["valid_from"] = _date(data.get("valid_from"), "The first day")
    fields["valid_until"] = _date(data.get("valid_until"), "The last day")
    if fields["valid_from"] and fields["valid_until"] and fields["valid_until"] < fields["valid_from"]:
        raise OfferInputError("The last day can't be before the first day.")

    # What it covers: dishes and categories of this outlet. An offer for
    # every outlet covers the whole menu, since each outlet has its own dishes.
    dish_ids = _ids(data.get("dish_ids"), "Dishes")
    category_ids = _ids(data.get("category_ids"), "Categories")
    if outlet is None and (dish_ids or category_ids):
        raise OfferInputError("An offer for every outlet covers the whole menu. "
                              "To pick dishes, make it for one outlet.")
    dishes = list(MenuItem.objects.filter(id__in=dish_ids, outlet=outlet)) if dish_ids else []
    categories = list(MenuCategory.objects.filter(id__in=category_ids, outlet=outlet)) if category_ids else []
    if len(dishes) != len(dish_ids) or len(categories) != len(category_ids):
        raise OfferInputError("A dish or category isn't on this outlet's menu.")

    # When it runs: one window of days and times (blank: always).
    days = _ids(data.get("days"), "Days")
    if any(d > 6 for d in days):
        raise OfferInputError("Days: pick from the list.")
    start, end = _time(data.get("start_time"), "The start"), _time(data.get("end_time"), "The end")
    if start and end and start == end:
        raise OfferInputError("The end can't be the same as the start.")
    if template == "happy_hour" and not (start or end):
        raise OfferInputError("A happy hour needs its hours.")
    window = None
    if days or start or end:
        window = {"days": "".join(str(d) for d in days), "start_time": start, "end_time": end}
    return fields, dishes, categories, window


def _offer_outlet(request, data):
    """The outlet an offer is for: this one, another of the owner's, or all."""
    from tenants.models import Outlet
    if data.get("all_outlets") is True:
        if request.user.role != "owner":
            raise OfferInputError("Only the owner can run an offer at every outlet.")
        return None
    outlet_id = data.get("outlet_id")
    if outlet_id and str(outlet_id) != str(request.user.outlet_id):
        if request.user.role != "owner":
            raise OfferInputError("A manager sets offers for their own outlet.")
        try:
            return Outlet.objects.get(id=outlet_id, tenant=request.user.tenant)
        except (Outlet.DoesNotExist, ValueError):
            raise OfferInputError("That outlet isn't yours.")
    return request.user.outlet


def _mine(request, offer_id):
    """An offer this user may change, or None."""
    from offers.models import Offer
    offer = Offer.objects.filter(id=offer_id, tenant=request.user.tenant, archived_at__isnull=True).first()
    if offer is None:
        return None
    if request.user.role != "owner" and offer.outlet_id != request.user.outlet_id:
        return None
    return offer


def _save_covers_and_hours(offer, dishes, categories, window):
    """Replace what an offer covers and when it runs."""
    from offers.models import OfferTarget, OfferWindow
    offer.targets.all().delete()
    offer.windows.all().delete()
    OfferTarget.objects.bulk_create(
        [OfferTarget(offer=offer, menu_item=d) for d in dishes]
        + [OfferTarget(offer=offer, category=c) for c in categories])
    if window:
        OfferWindow.objects.create(offer=offer, **window)


def _snapshot(offer):
    """An offer as an owner reads it, field by field, for its change record."""
    def number(value):
        return "" if value is None else f"{value:.2f}".rstrip("0").rstrip(".")
    targets = list(offer.targets.select_related("menu_item", "category"))
    return {
        "Name": offer.name,
        "Kind": offer.summary,
        "Covers": ", ".join(sorted(f"{t.category.name} (category)" if t.category_id else t.menu_item.name
                                   for t in targets)) or "Whole menu",
        "When": "; ".join(str(w) for w in offer.windows.all()) or "Any time",
        "First day": offer.valid_from.isoformat() if offer.valid_from else "",
        "Last day": offer.valid_until.isoformat() if offer.valid_until else "",
        "Priority": str(offer.priority),
        "Outlet": offer.outlet.name if offer.outlet_id else "All outlets",
    }


def _differences(before, after):
    return [{"field": key, "before": before[key], "after": after[key]}
            for key in before if before[key] != after[key]]


def form_data(offer):
    """An offer as the Setup form holds it, so Edit can load it back."""
    window = next(iter(offer.windows.all()), None)
    targets = list(offer.targets.all())
    if offer.kind == "percent_off":
        template = "happy_hour" if window and (window.start_time or window.end_time) else "percent_off"
    else:
        template = offer.kind
    return {
        "id": offer.id, "template": template, "name": offer.name,
        "buy_qty": offer.buy_qty or 2, "free_qty": offer.free_qty or 1,
        "percent": str(offer.percent) if offer.percent is not None else "",
        "amount": str(offer.amount) if offer.amount is not None else "",
        "dish_ids": sorted(t.menu_item_id for t in targets if t.menu_item_id),
        "category_ids": sorted(t.category_id for t in targets if t.category_id),
        "days": sorted(window.day_set) if window else [],
        "start_time": window.start_time.strftime("%H:%M") if window and window.start_time else "",
        "end_time": window.end_time.strftime("%H:%M") if window and window.end_time else "",
        "valid_from": offer.valid_from.isoformat() if offer.valid_from else "",
        "valid_until": offer.valid_until.isoformat() if offer.valid_until else "",
        "priority": offer.priority, "all_outlets": offer.outlet_id is None,
    }


def retotal_open_bills(tenant, outlet):
    """Re-total the open bills an offer could reach (one outlet, or every
    outlet for an all-outlets offer), so a corrected offer shows on them now.
    Paid bills are never re-totalled. Returns how many."""
    from orders.models import Order
    open_ids = Order.objects.filter(tenant=tenant, status__in=["open", "billing"])
    if outlet is not None:
        open_ids = open_ids.filter(outlet=outlet)
    count = 0
    for order_id in list(open_ids.values_list("id", flat=True)):
        with transaction.atomic():
            order = Order.objects.select_for_update().filter(id=order_id, status__in=["open", "billing"]).first()
            if order is not None:
                order.recalculate_totals()
                count += 1
    return count


@login_required
@tenant_required
@feature_required("offers")
def setup_offers(request):
    if request.user.role not in MANAGERS:
        return redirect("/setup/")
    from django.db.models import Q

    from menu.models import MenuCategory, MenuItem
    from offers.models import Offer
    from orders.services.tax_service import sale_tax_map
    from tenants.models import Outlet

    tenant, outlet = request.user.tenant, request.user.outlet
    offers = (
        Offer.objects.filter(tenant=tenant, archived_at__isnull=True)
        .filter(Q(outlet=outlet) | Q(outlet__isnull=True))
        .select_related("outlet")
        .prefetch_related("targets__menu_item", "targets__category", "windows", "changes__changed_by")
        .order_by("-is_active", "-priority", "name")
    )
    categories = MenuCategory.objects.filter(tenant=tenant, outlet=outlet).order_by("display_order", "name")
    dishes = list(MenuItem.objects.filter(tenant=tenant, outlet=outlet).select_related("category", "vat_class")
                  .order_by("category__display_order", "category__name", "name"))
    from core.utils import _cutoff_hour
    return render(request, "setup/setup_offers.html", {
        "offers": offers,
        "offer_forms": {str(o.id): form_data(o) for o in offers},
        "categories": categories,
        "dishes": dishes,
        "example_menu": [{"id": d.id, "name": d.name, "price": str(d.price), "category": d.category_id}
                         for d in dishes],
        "dish_tax": sale_tax_map(dishes, tenant),
        "outlets": Outlet.objects.filter(tenant=tenant).order_by("name"),
        "current_outlet": outlet,
        "cutoff_hour": _cutoff_hour(outlet),
    })


@login_required
@tenant_required
@feature_required("offers")
@require_POST
def offer_create(request):
    if request.user.role not in MANAGERS:
        return HttpResponseForbidden()
    from offers.models import Offer
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Nothing to save."}, status=400)
    try:
        outlet = _offer_outlet(request, data if isinstance(data, dict) else {})
        fields, dishes, categories, window = read_offer_fields(data, outlet)
        with transaction.atomic():
            offer = Offer(tenant=request.user.tenant, outlet=outlet, created_by=request.user, **fields)
            offer.full_clean()
            offer.save()
            _save_covers_and_hours(offer, dishes, categories, window)
    except OfferInputError as e:
        return error_response(e, 400)
    except ValidationError as e:
        return error_response(e, 400)
    logger.info("User %s created offer %r (%s)", request.user.username, offer.name, offer.summary)
    return JsonResponse({"success": True, "id": offer.id, "name": offer.name, "summary": offer.summary})


@login_required
@tenant_required
@feature_required("offers")
@require_POST
def offer_update(request, offer_id):
    """Fix an offer, checked exactly like a new one. Paid bills keep what
    they were billed; open bills are re-totalled now, so a mistake (50% for
    5%) stops costing money at once. Every change is recorded."""
    from offers.models import OfferChange
    if request.user.role not in MANAGERS:
        return HttpResponseForbidden()
    offer = _mine(request, offer_id)
    if offer is None:
        return JsonResponse({"error": "Offer not found"}, status=404)
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"error": "Nothing to save."}, status=400)
    try:
        outlet = _offer_outlet(request, data if isinstance(data, dict) else {})
        fields, dishes, categories, window = read_offer_fields(data, outlet)
        with transaction.atomic():
            before = _snapshot(offer)
            old_outlet = offer.outlet
            offer.outlet = outlet
            for name, value in fields.items():
                setattr(offer, name, value)
            offer.full_clean()
            offer.save()
            _save_covers_and_hours(offer, dishes, categories, window)
            offer = type(offer).objects.get(pk=offer.pk)
            changes = _differences(before, _snapshot(offer))
            if changes:
                OfferChange.objects.create(offer=offer, changed_by=request.user, changes=changes)
    except OfferInputError as e:
        return error_response(e, 400)
    except ValidationError as e:
        return error_response(e, 400)
    bills = 0
    if changes:
        # The outlet it was for and the one it is for now both gain or lose it.
        scopes = {old_outlet, outlet}
        if None in scopes:
            bills = retotal_open_bills(request.user.tenant, None)
        else:
            bills = sum(retotal_open_bills(request.user.tenant, o) for o in scopes)
    logger.warning("User %s edited offer %r: %s (open bills re-totalled: %s)",
                   request.user.username, offer.name, changes, bills)
    return JsonResponse({"success": True, "id": offer.id, "name": offer.name, "summary": offer.summary,
                         "changes": changes, "bills": bills})


@login_required
@tenant_required
@feature_required("offers")
@require_POST
def offer_toggle(request, offer_id):
    """Switch an offer on or off. Off keeps it on the dishes already ordered."""
    if request.user.role not in MANAGERS:
        return HttpResponseForbidden()
    offer = _mine(request, offer_id)
    if offer is None:
        return JsonResponse({"error": "Offer not found"}, status=404)
    offer.is_active = not offer.is_active
    offer.save()
    return JsonResponse({"success": True, "is_active": offer.is_active})


@login_required
@tenant_required
@feature_required("offers")
@require_POST
def offer_delete(request, offer_id):
    """Archived, not deleted: bills that used it keep it."""
    if request.user.role not in MANAGERS:
        return HttpResponseForbidden()
    offer = _mine(request, offer_id)
    if offer is None:
        return JsonResponse({"error": "Offer not found"}, status=404)
    offer.archive()
    return JsonResponse({"success": True})


@login_required
@tenant_required
@feature_required("offers")
@require_POST
def offers_pause_all(request):
    """For a bad night: switch every offer of this outlet (and, for the
    owner, the all-outlet ones) off at once. Dishes already ordered keep
    theirs; each can be switched on again on its own."""
    if request.user.role not in MANAGERS:
        return HttpResponseForbidden()
    from django.db.models import Q

    from offers.models import Offer
    scope = Q(outlet=request.user.outlet)
    if request.user.role == "owner":
        scope |= Q(outlet__isnull=True)
    now = timezone.now()
    paused = (Offer.objects.filter(tenant=request.user.tenant, is_active=True, archived_at__isnull=True)
              .filter(scope).update(is_active=False, paused_at=now))
    logger.warning("User %s paused all offers (%s)", request.user.username, paused)
    return JsonResponse({"success": True, "paused": paused})
