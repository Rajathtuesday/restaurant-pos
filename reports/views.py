# reports/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from reports.services.sales_reports import daily_sales, hourly_sales
from reports.services.item_reports import top_items
from django.http import HttpResponseForbidden


@login_required
def dashboard(request):
    
    if request.user.role not in ["owner", "manager"]:
        # return render(request, "reports/permission_denied.html")
        return HttpResponseForbidden("You do not have permission to access this page.")
        
    
    tenant = request.user.tenant
    outlet = request.user.outlet

    sales = daily_sales(tenant, outlet)
    items = top_items(tenant, outlet)
    hourly = hourly_sales(tenant, outlet)

    return render(
        request,
        "reports/dashboard.html",
        {
            "sales": sales,
            "items": items,
            "hourly_sales": hourly
        }
    )