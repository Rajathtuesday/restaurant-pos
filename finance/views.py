# finance/views.py
import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from core.decorators import tenant_required, role_required, feature_required
from tenants.models import Outlet
from .models import Expense


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("advanced_reports")
def expense_list(request):
    """Expense ledger — list + the create form, same single-page pattern as
    setup_staff.html (create form alongside the existing list)."""
    expenses = Expense.objects.filter(tenant=request.user.tenant).select_related("outlet")[:200]
    outlets = Outlet.objects.filter(tenant=request.user.tenant)
    return render(request, "finance/expense_list.html", {
        "expenses": expenses,
        "outlets": outlets,
        "categories": Expense.CATEGORY_CHOICES,
    })


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("advanced_reports")
@require_POST
def expense_create(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid request."}, status=400)

    category = data.get("category")
    if category not in dict(Expense.CATEGORY_CHOICES):
        return JsonResponse({"error": "Invalid category."}, status=400)

    try:
        amount = Decimal(str(data.get("amount", "")))
    except InvalidOperation:
        return JsonResponse({"error": "Invalid amount."}, status=400)
    if amount <= 0:
        return JsonResponse({"error": "Amount must be positive."}, status=400)

    expense_date = data.get("expense_date")
    if not expense_date:
        return JsonResponse({"error": "Expense date is required."}, status=400)

    outlet = None
    outlet_id = data.get("outlet_id")
    if outlet_id:
        try:
            outlet = Outlet.objects.get(id=outlet_id, tenant=request.user.tenant)
        except Outlet.DoesNotExist:
            return JsonResponse({"error": "Invalid outlet."}, status=400)

    try:
        expense = Expense.objects.create(
            tenant=request.user.tenant,
            outlet=outlet,
            category=category,
            amount=amount,
            is_recurring=bool(data.get("is_recurring", False)),
            expense_date=expense_date,
            notes=(data.get("notes") or "").strip()[:255],
            created_by=request.user,
        )
    except Exception:
        return JsonResponse({"error": "Could not save expense — check the date is valid (YYYY-MM-DD)."}, status=400)

    return JsonResponse({
        "success": True,
        "id": expense.id,
        "message": f"{expense.get_category_display()} expense of ₹{expense.amount} recorded.",
    })


@login_required
@tenant_required
@role_required("owner", "manager")
@feature_required("advanced_reports")
@require_POST
def expense_delete(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id, tenant=request.user.tenant)
    expense.delete()
    return JsonResponse({"success": True})
