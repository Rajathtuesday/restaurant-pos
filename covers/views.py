"""The door's cover screen, the printable slip, and spending a cover on a bill."""
import json
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import feature_required, role_required, tenant_required
from core.errors import error_response
from core.utils import get_business_date
from covers.models import PAID_BY, Cover
from covers.services import CoverError, night_summary, redeem_cover, sell_cover
from orders.models import Order

STAFF = ("owner", "manager", "cashier", "captain")


@login_required
@tenant_required
@feature_required("cover_charge")
@role_required(*STAFF)
def covers(request):
    outlet = request.user.outlet
    if request.method == "POST":
        try:
            cover = sell_cover(
                request.user, outlet, request.POST.get("amount"), request.POST.get("paid_by"),
                guests=request.POST.get("guests") or 1, guest_name=request.POST.get("guest_name", ""),
                guest_phone=request.POST.get("guest_phone", ""),
            )
        except CoverError as e:
            messages.error(request, e.message)
            return redirect("covers")
        messages.success(request, f"Cover {cover.code} for Rs {cover.amount}. Give the guest the slip.")
        return redirect(f"{request.path}?slip={cover.id}")

    today = get_business_date(timezone.now(), outlet)
    day = today
    if request.GET.get("date"):
        try:
            day = date.fromisoformat(request.GET["date"])
        except ValueError:
            pass
    slip = None
    if request.GET.get("slip", "").isdigit():
        slip = Cover.objects.filter(id=int(request.GET["slip"]), tenant=request.user.tenant, outlet=outlet).first()
    return render(request, "covers/covers.html", {
        **night_summary(request.user.tenant, outlet, day),
        "day": day, "today": today, "slip": slip, "paid_by_choices": PAID_BY,
    })


@login_required
@tenant_required
@feature_required("cover_charge")
@role_required(*STAFF)
@require_POST
def redeem(request, order_id):
    """Body {"code": "K7Q4M"}: pay what is still due on this bill from the
    cover, as far as it goes."""
    order = get_object_or_404(Order, id=order_id, tenant=request.user.tenant, outlet=request.user.outlet)
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        data = {}
    try:
        redemption = redeem_cover(request.user, order, data.get("code") if isinstance(data, dict) else None)
    except CoverError as e:
        return error_response(e, 400)
    except Exception as e:      # a refusal from process_payment (a ValidationError) is shown as it is
        return error_response(e, 400)
    order.refresh_from_db()
    return JsonResponse({
        "success": True,
        "amount": float(redemption.amount),
        "left_on_cover": float(redemption.cover.balance()),
        "order_closed": order.status == "closed",
    })
