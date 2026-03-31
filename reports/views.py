# reports/views.py
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import HttpResponseForbidden
from reports.services.sales_reports import daily_sales, hourly_sales
from reports.services.item_reports import top_items
from reports.services.table_reports import table_turnover
from reports.services.category_reports import category_sales
from reports.services.waiter_reports import waiter_performance
from reports.services.waiter_reports import waiter_performance
from tenants.models import Outlet
from django.utils import timezone
from datetime import timedelta

@login_required
def dashboard(request):

    if request.user.role not in ["owner", "manager"]:
        return HttpResponseForbidden()

    tenant = request.user.tenant

    # date filter
    date_filter = request.GET.get("date_filter", "today")
    
    start_date = timezone.now().date()
    end_date = timezone.now().date()

    if date_filter == "yesterday":
        start_date = start_date - timedelta(days=1)
        end_date = start_date
    elif date_filter == "weekly":
        start_date = start_date - timedelta(days=7)
    elif date_filter == "monthly":
        start_date = start_date - timedelta(days=30)
    elif date_filter == "custom":
        custom_start = request.GET.get("start_date")
        custom_end = request.GET.get("end_date")
        if custom_start and custom_end:
            from datetime import datetime
            try:
                start_date = datetime.strptime(custom_start, "%Y-%m-%d").date()
                end_date = datetime.strptime(custom_end, "%Y-%m-%d").date()
            except ValueError:
                pass # fallback to today

    # outlet selection
    outlet_id = request.GET.get("outlet")

    if request.user.role == "owner":
        outlets = Outlet.objects.filter(tenant=tenant)

        if outlet_id:
            outlet = outlets.filter(id=outlet_id).first()

            # 🔴 IMPORTANT FIX (security + correctness)
            if not outlet:
                return HttpResponseForbidden("Invalid outlet")
        else:
            outlet = None  # means ALL outlets

    else:
        outlet = request.user.outlet
        outlets = [outlet]

    selected_outlet = outlet

    sales = daily_sales(tenant, selected_outlet, start_date, end_date)
    items = top_items(tenant, selected_outlet, start_date, end_date)
    hourly = hourly_sales(tenant, selected_outlet, start_date, end_date)
    table_stats = table_turnover(tenant, selected_outlet, start_date, end_date)
    categories = category_sales(tenant, selected_outlet, start_date, end_date)
    waiters = waiter_performance(tenant, selected_outlet, start_date, end_date)

    return render(request, "reports/dashboard.html", {
        "sales": sales,
        "items": items,
        "hourly_sales": hourly,
        "table_stats": table_stats,
        "categories": categories,
        "waiters": waiters,
        "outlets": outlets,
        "current_outlet": outlet,
        "date_filter": date_filter,
        "start_date": start_date,
        "end_date": end_date
    })