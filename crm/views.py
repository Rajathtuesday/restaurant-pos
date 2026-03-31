# crm/views.py
import json
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from core.decorators import tenant_required
from .models import Guest, LoyaltyTransaction

# 1 point per ₹10 spent
POINTS_PER_RUPEE = 0.1


@login_required
@tenant_required
def crm_dashboard(request):
    """Guest list searchable by name/phone."""
    if request.user.role not in ("manager", "owner", "cashier") and not request.user.is_superuser:
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden()

    query = request.GET.get("q", "").strip()
    guests = Guest.objects.filter(tenant=request.user.tenant).order_by("-created_at")
    if query:
        guests = guests.filter(phone__icontains=query) | guests.filter(name__icontains=query)

    return render(request, "crm/crm_dashboard.html", {"guests": guests, "query": query})


@login_required
@tenant_required
def guest_profile(request, guest_id):
    """Detailed guest loyalty history."""
    try:
        guest = Guest.objects.get(id=guest_id, tenant=request.user.tenant)
        transactions = guest.transactions.all()[:50]
        return render(request, "crm/guest_profile.html", {"guest": guest, "transactions": transactions})
    except Guest.DoesNotExist:
        from django.http import Http404
        raise Http404


@login_required
@tenant_required
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
@require_POST
def link_guest_to_order(request, order_id):
    """
    Links a guest to a completed/billing order.
    Creates guest if new. Awards loyalty points based on grand_total.
    """
    from orders.models import Order
    try:
        data = json.loads(request.body)
        phone = data.get("phone", "").strip()
        name = data.get("name", "").strip()
        redeem_points = int(data.get("redeem_points", 0))

        if not phone:
            return JsonResponse({"error": "Phone required"}, status=400)

        order = Order.objects.get(
            id=order_id, tenant=request.user.tenant, outlet=request.user.outlet
        )

        guest, created = Guest.objects.get_or_create(
            tenant=request.user.tenant,
            phone=phone,
            defaults={"name": name}
        )
        if name and not guest.name:
            guest.name = name
            guest.save(update_fields=["name"])

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
            guest.total_points += earned

        if redeem_points > 0:
            LoyaltyTransaction.objects.create(
                guest=guest, order=order,
                transaction_type="redeem",
                points=-redeem_points,
                description=f"Redeemed on Order #{order.order_number}"
            )
            guest.total_points -= redeem_points

        guest.total_spent += order.grand_total
        guest.visit_count += 1
        guest.save(update_fields=["total_points", "total_spent", "visit_count"])

        return JsonResponse({
            "success": True,
            "guest_id": guest.id,
            "points_earned": earned,
            "points_redeemed": redeem_points,
            "total_points": guest.total_points,
        })

    except Order.DoesNotExist:
        return JsonResponse({"error": "Order not found"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=400)
