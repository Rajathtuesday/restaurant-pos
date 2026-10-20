"""The nightly bar count screens (inventory/bar_count.py)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.decorators import role_required, tenant_required
from inventory.bar_count import CountError, bar_items, count_report, save_count
from inventory.kegs import KegError, change_keg, keg_board
from inventory.models import BarCount


@login_required
@tenant_required
@role_required("owner", "manager")
def bar_count(request):
    """The blind count form, and saving it."""
    outlet = request.user.outlet
    items = list(bar_items(request.user.tenant, outlet))
    if request.method == "POST":
        entries = {
            item.id: (request.POST.get(f"bottles_{item.id}"), request.POST.get(f"amount_{item.id}"))
            for item in items
        }
        try:
            count = save_count(request.user, outlet, entries)
        except CountError as e:
            messages.error(request, e.message)
            return render(request, "inventory/bar_count.html", {"rows": _rows(items, request.POST)})
        return redirect("bar_count_result", count_id=count.id)
    return render(request, "inventory/bar_count.html", {"rows": _rows(items, {})})


def _rows(items, typed):
    """Each bottle with what was typed for it (kept when a count is refused)."""
    return [{"item": item, "bottles": typed.get(f"bottles_{item.id}", ""), "amount": typed.get(f"amount_{item.id}", "")}
            for item in items]


@login_required
@tenant_required
@role_required("owner", "manager")
def bar_count_result(request, count_id):
    count = get_object_or_404(BarCount, id=count_id, tenant=request.user.tenant, outlet=request.user.outlet)
    return render(request, "inventory/bar_count_result.html", count_report(count))


@login_required
@tenant_required
@role_required("owner", "manager")
def bar_count_list(request):
    counts = (BarCount.objects.filter(tenant=request.user.tenant, outlet=request.user.outlet)
              .select_related("counted_by").prefetch_related("lines")[:60])
    reports = [count_report(c) for c in counts]
    return render(request, "inventory/bar_count_list.html", {"reports": reports})


@login_required
@tenant_required
@role_required("owner", "manager", "cashier", "captain")
def kegs(request):
    """Draught kegs: what the one on tap has sold, and each finished keg's
    yield and loss (inventory/kegs.py)."""
    return render(request, "inventory/kegs.html", keg_board(request.user.tenant, request.user.outlet))


@login_required
@tenant_required
@role_required("owner", "manager", "cashier", "captain")
@require_POST
def keg_change(request, item_id):
    try:
        change = change_keg(request.user, request.user.outlet, item_id)
    except KegError as e:
        messages.error(request, e.message)
        return redirect("kegs")
    if change.lost:
        messages.success(request, f"New keg of {change.item.name} tapped. The last one sold "
                                  f"{change.sold.normalize():f} {change.item.unit}; {change.lost.normalize():f} "
                                  f"{change.item.unit} was not sold.")
    else:
        messages.success(request, f"New keg of {change.item.name} tapped.")
    return redirect("kegs")


@login_required
@tenant_required
@role_required("owner", "manager")
def liquor_register(request):
    """The liquor stock register as an Excel file (inventory/liquor_register.py).
    ?from=YYYY-MM-DD&to=YYYY-MM-DD; the last 7 days by default, at most 62."""
    from datetime import date, timedelta

    from django.http import HttpResponse
    from django.utils import timezone

    from core.utils import get_business_date
    from inventory.liquor_register import MAX_DAYS, register_workbook

    outlet = request.user.outlet
    today = get_business_date(timezone.now(), outlet)
    try:
        end = date.fromisoformat(request.GET.get("to") or today.isoformat())
        start = date.fromisoformat(request.GET.get("from") or (end - timedelta(days=6)).isoformat())
    except ValueError:
        messages.error(request, "Pick the dates again: one of them wasn't a date.")
        return redirect("bar_count_list")
    if start > end:
        start, end = end, start
    if (end - start).days >= MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS - 1)
    wb = register_workbook(request.user.tenant, outlet, start, end)
    response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="liquor_register_{start}_to_{end}.xlsx"'
    wb.save(response)
    return response
