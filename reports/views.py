# reports/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import HttpResponseForbidden
from reports.services.sales_reports import daily_sales, hourly_sales
from reports.services.item_reports import top_items
from reports.services.table_reports import table_turnover
from reports.services.category_reports import category_sales
from reports.services.waiter_reports import waiter_performance
from tenants.models import Outlet

@login_required
def dashboard(request):

    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()

    tenant = request.user.tenant

    # outlet selection
    outlet_id = request.GET.get("outlet")

    if request.user.role == "owner":
        outlets = Outlet.objects.filter(tenant=tenant)

        if outlet_id:
            outlet = outlets.filter(id=outlet_id).first()
        else:
            outlet = None
    else:
        outlet = request.user.outlet
        outlets = [outlet]

    outlet_filter = outlet

    sales = daily_sales(tenant, outlet_filter)
    items = top_items(tenant, outlet)
    hourly = hourly_sales(tenant, outlet)
    table_stats = table_turnover(tenant, outlet)
    categories = category_sales(tenant, outlet)
    waiters = waiter_performance(tenant, outlet)

    return render(request, "reports/dashboard.html", {
        "sales": sales,
        "items": items,
        "hourly_sales": hourly,
        "table_stats": table_stats,
        "categories": categories,
        "waiters": waiters,
        "outlets": outlets,
        "current_outlet": outlet
    })