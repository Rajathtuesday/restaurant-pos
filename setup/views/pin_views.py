"""
A manager's or owner's approval PIN (orders/services/approvals.py): typed on
a cashier's screen to approve taking money off a printed bill.

Each manager sets their own, after typing their password, so nobody else
knows it. The owner can clear a manager's PIN (a manager who left, or one
who forgot it); the manager then sets a new one.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.demo_restrictions import BLOCKED_MESSAGE, is_demo_trailer
from accounts.models import User
from core.decorators import role_required, tenant_required
from orders.services.approvals import PIN_LOCK, PIN_TRIES, PinError, clear_pin, set_pin


@login_required
@tenant_required
@role_required("owner", "manager")
def my_approval_pin(request):
    user = request.user
    if request.method == "POST":
        if is_demo_trailer(request):
            messages.info(request, BLOCKED_MESSAGE)
            return redirect("my_approval_pin")
        action = request.POST.get("action")
        if not user.check_password(request.POST.get("password") or ""):
            messages.error(request, "That isn't your password. Your PIN was not changed.")
            return redirect("my_approval_pin")
        if action == "clear":
            clear_pin(user)
            messages.success(request, "Your PIN is removed. Cashiers can't ask you to approve until you set one.")
            return redirect("my_approval_pin")
        pin = (request.POST.get("pin") or "").strip()
        if pin != (request.POST.get("pin_again") or "").strip():
            messages.error(request, "The two PINs don't match.")
            return redirect("my_approval_pin")
        try:
            set_pin(user, pin)
        except PinError as e:
            messages.error(request, e.message)
            return redirect("my_approval_pin")
        messages.success(request, "Your approval PIN is set.")
        return redirect("my_approval_pin")

    return render(request, "setup/approval_pin.html", {
        "has_pin": bool(user.approval_pin),
        "pin_tries": PIN_TRIES,
        "pin_lock_minutes": int(PIN_LOCK.total_seconds() // 60),
        "managers": (
            User.objects.filter(tenant=user.tenant, role="manager").select_related("outlet").order_by("username")
            if user.role == "owner" else []
        ),
    })


@login_required
@tenant_required
@role_required("owner")
@require_POST
def clear_staff_pin(request, user_id):
    if is_demo_trailer(request):
        messages.info(request, BLOCKED_MESSAGE)
        return redirect("my_approval_pin")
    manager = get_object_or_404(User, id=user_id, tenant=request.user.tenant, role="manager")
    clear_pin(manager)
    messages.success(request, f"{manager.username}'s PIN is cleared. They can set a new one.")
    return redirect("my_approval_pin")
