# crm/views.py
import json
import logging
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, feature_required
from .models import Guest, LoyaltyTransaction

logger = logging.getLogger("pos.crm")

# 1 point per ₹10 spent
POINTS_PER_RUPEE = 0.1


@login_required
@tenant_required
@feature_required("crm")
def crm_dashboard(request):
    """Guest list searchable by name/phone."""
    if request.user.role not in ("manager", "owner", "cashier", "captain") and not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    query = request.GET.get("q", "").strip()
    guests = Guest.objects.filter(tenant=request.user.tenant).order_by("-created_at")
    if query:
        guests = guests.filter(phone__icontains=query) | guests.filter(name__icontains=query)

    return render(request, "crm/crm_dashboard.html", {"guests": guests, "query": query})


@login_required
@tenant_required
@feature_required("crm")
def guest_profile(request, guest_id):
    """Detailed guest loyalty history."""
    if request.user.role not in ("manager", "owner", "cashier", "captain") and not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    try:
        guest = Guest.objects.get(id=guest_id, tenant=request.user.tenant)
        transactions = guest.transactions.all()[:50]
        return render(request, "crm/guest_profile.html", {"guest": guest, "transactions": transactions})
    except Guest.DoesNotExist:
        from django.http import Http404
        raise Http404


@login_required
@tenant_required
@feature_required("crm")
def guest_lookup(request):
    """API: Look up a guest by phone — used in the billing/bill modal."""
    phone = request.GET.get("phone", "").strip()
    if not phone:
        return JsonResponse({"error": "Phone required"}, status=400)

    guest = Guest.objects.filter(tenant=request.user.tenant, phone=phone).first()
    if guest:
        return JsonResponse({
            "found": True,
            "id": guest.id,
            "name": guest.name,
            "phone": guest.phone,
            "points": guest.total_points,
            "visits": guest.visit_count,
            "total_spent": float(guest.total_spent),
        })
    return JsonResponse({"found": False})


@login_required
@tenant_required
@feature_required("crm")
@require_POST
def link_guest_to_order(request, order_id):
    """
    Links a guest to a completed/billing order.
    Creates guest if new. Awards loyalty points based on grand_total.
    """
    from orders.models import Order
    from django.db import transaction
    from django.db.models import F

    try:
        data = json.loads(request.body)
        phone = data.get("phone", "").strip()
        name = data.get("name", "").strip()
        redeem_points = int(data.get("redeem_points", 0))

        if not phone:
            return JsonResponse({"error": "Phone required"}, status=400)

        with transaction.atomic():
            order = Order.objects.get(
                id=order_id, tenant=request.user.tenant, outlet=request.user.outlet
            )

            guest, created = Guest.objects.select_for_update().get_or_create(
                tenant=request.user.tenant,
                phone=phone,
                defaults={"name": name}
            )
            
            if name and not guest.name:
                guest.name = name
                guest.save(update_fields=["name"])

            # Guard: don't award points twice
            if order.loyalty_transactions.filter(transaction_type="earn").exists():
                return JsonResponse(
                    {"error": "Points already awarded for this order"}, 
                    status=400
                )

            # Points earned = 1 per ₹10
            earned = int(float(order.grand_total) * POINTS_PER_RUPEE)

            # Redeem validation
            if redeem_points > guest.total_points:
                return JsonResponse({"error": "Not enough points"}, status=400)

            # Record transactions
            if earned > 0:
                LoyaltyTransaction.objects.create(
                    guest=guest, order=order,
                    transaction_type="earn",
                    points=earned,
                    description=f"Order #{order.order_number}"
                )

            if redeem_points > 0:
                LoyaltyTransaction.objects.create(
                    guest=guest, order=order,
                    transaction_type="redeem",
                    points=-redeem_points,
                    description=f"Redeemed on Order #{order.order_number}"
                )

            # Atomic update
            Guest.objects.filter(pk=guest.pk).update(
                total_points=F("total_points") + earned - redeem_points,
                total_spent=F("total_spent") + order.grand_total,
                visit_count=F("visit_count") + 1
            )
            guest.refresh_from_db()

            return JsonResponse({
                "success": True,
                "guest_id": guest.id,
                "points_earned": earned,
                "points_redeemed": redeem_points,
                "total_points": guest.total_points,
            })

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid redeem_points value."}, status=400)
    except Exception:
        logger.exception("Error linking guest to order #%s", order_id)
        return JsonResponse({"error": "Could not link guest to this order. Please try again."}, status=500)


@login_required
@tenant_required
@feature_required("reservations")
def reservation_list(request):
    """View to list and manage table bookings."""
    if request.user.role not in ("manager", "owner", "cashier", "captain") and not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    from .models import Reservation
    from orders.models import Table
    from django.utils import timezone

    date_str = request.GET.get("date")
    if date_str:
        try:
            from datetime import datetime
            view_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            view_date = timezone.localdate()
    else:
        view_date = timezone.localdate()

    reservations = Reservation.objects.filter(
        tenant=request.user.tenant,
        outlet=request.user.outlet,
        reservation_time__date=view_date
    ).select_related("guest", "table").order_by("reservation_time")

    tables = Table.objects.filter(tenant=request.user.tenant, outlet=request.user.outlet, is_active=True)

    return render(request, "crm/reservations.html", {
        "reservations": reservations,
        "tables": tables,
        "view_date": view_date
    })


@login_required
@tenant_required
@feature_required("reservations")
@require_POST
def create_reservation(request):
    """API to create a new reservation."""
    from .models import Reservation, Guest
    from orders.models import Table
    from django.db import transaction
    from django.utils import timezone
    from datetime import datetime, timedelta

    if request.user.role not in ("manager", "owner", "cashier", "captain") and not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

    try:
        data = json.loads(request.body)
        phone = data.get("phone", "").strip()
        name = data.get("name", "").strip()
        table_id = data.get("table_id")
        res_time_str = data.get("reservation_time")
        guests_count = int(data.get("guests", 2))

        if not phone or not res_time_str:
            return JsonResponse({"error": "Phone and Time are required"}, status=400)

        res_time = timezone.make_aware(datetime.strptime(res_time_str, "%Y-%m-%dT%H:%M"))

        guest, _ = Guest.objects.get_or_create(
            tenant=request.user.tenant,
            phone=phone,
            defaults={"name": name}
        )

        with transaction.atomic():
            if table_id:
                # Lock the table row itself to serialize concurrent
                # reservation attempts for the same table -- select_for_update()
                # on the conflict-check query alone wouldn't close this race:
                # if zero rows currently conflict, there's nothing to lock, so
                # two concurrent requests could both pass the .exists() check
                # below before either commits (the same structural TOCTOU bug
                # as the Razorpay webhook double-payment race, just for table
                # bookings instead of money). Locking the Table row -- which
                # always exists once validated -- forces a second concurrent
                # request for the same table to wait for the first to finish,
                # the same pattern shifts/views.py::open_cash_session already
                # uses for CashSession. This get() also does the "table
                # belongs to this tenant/outlet" validation the old separate
                # .exists() check did -- a crafted table_id 404s the same way.
                try:
                    table = Table.objects.select_for_update().get(
                        id=table_id, tenant=request.user.tenant, outlet=request.user.outlet
                    )
                except Table.DoesNotExist:
                    return JsonResponse({"error": "Invalid table"}, status=400)

                conflict = Reservation.objects.filter(
                    tenant=request.user.tenant,
                    outlet=request.user.outlet,
                    table_id=table.id,
                    status__in=["pending", "confirmed"],
                    reservation_time__range=(
                        res_time - timedelta(hours=1),
                        res_time + timedelta(hours=1)
                    )
                ).exists()
                if conflict:
                    return JsonResponse(
                        {"error": "Table already booked around this time"},
                        status=409
                    )

            reservation = Reservation.objects.create(
                tenant=request.user.tenant,
                outlet=request.user.outlet,
                guest=guest,
                table_id=table_id,
                reservation_time=res_time,
                number_of_guests=guests_count,
                created_by=request.user
            )

        return JsonResponse({"success": True, "reservation_id": reservation.id})

    except (ValueError, TypeError):
        # A malformed reservation_time or non-numeric guests count is a bad
        # request from the client, not a server failure -- keep that a 400,
        # separate from the catch-all below, rather than flattening every
        # exception into the same generic 500.
        return JsonResponse({"error": "Invalid reservation time or guest count."}, status=400)
    except Exception:
        logger.exception("Error creating reservation")
        return JsonResponse({"error": "Reservation could not be created. Please try again."}, status=500)


# A reservation's status is a small state machine, not a free-for-all field --
# this is the only map that may move it, so an invalid jump (e.g. reopening a
# cancelled booking) fails loudly instead of silently corrupting the timeline.
RESERVATION_TRANSITIONS = {
    "pending":   {"confirmed", "cancelled"},
    "confirmed": {"seated", "cancelled", "no_show"},
    "seated":    set(),
    "cancelled": set(),
    "no_show":   set(),
}


@login_required
@tenant_required
@feature_required("reservations")
@require_POST
def update_reservation_status(request, reservation_id):
    """Moves a reservation through pending -> confirmed -> seated (or
    cancelled/no_show), the lifecycle the model already promises via
    STATUS_CHOICES but that, until now, had no endpoint to drive it."""
    from .models import Reservation

    if request.user.role not in ("manager", "owner", "cashier", "captain") and not request.user.is_superuser:
        return JsonResponse({"error": "Permission denied"}, status=403)

    reservation = get_object_or_404(
        Reservation, id=reservation_id,
        tenant=request.user.tenant, outlet=request.user.outlet,
    )

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)

    new_status = data.get("status")
    allowed_next = RESERVATION_TRANSITIONS.get(reservation.status, set())
    if new_status not in allowed_next:
        return JsonResponse(
            {"error": f"Can't move from {reservation.status} to {new_status}."},
            status=400,
        )

    reservation.status = new_status
    reservation.save(update_fields=["status"])

    if new_status == "seated" and reservation.table_id:
        # Best-effort convenience nudge, not authoritative -- never clobber a
        # table that's already mid-service from an unrelated walk-in. The
        # reservation's own status field is the source of truth either way.
        from orders.models import Table
        Table.objects.filter(id=reservation.table_id, state="free").update(state="ordering")

    return JsonResponse({"success": True, "status": reservation.status})
