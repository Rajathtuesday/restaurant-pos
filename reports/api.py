# reports/api.py

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from datetime import timedelta
from django.core.serializers.json import DjangoJSONEncoder

from tenants.models import Outlet
from core.decorators import tenant_required
from reports.services.sales_reports import daily_sales, hourly_sales
from reports.services.item_reports import top_items
from reports.services.table_reports import table_turnover
from reports.services.category_reports import category_sales
from reports.services.waiter_reports import waiter_performance
from reports.services.kitchen_reports import kitchen_performance, top_kitchen_items

@login_required
@tenant_required
def api_dashboard(request):
    if request.user.role not in ["owner", "manager"]:
        return JsonResponse({"error": "Permission denied"}, status=403)

    tenant = request.user.tenant
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
                pass

    outlet_id = request.GET.get("outlet")
    if request.user.role == "owner":
        outlets = Outlet.objects.filter(tenant=tenant)
        if outlet_id:
            outlet = outlets.filter(id=outlet_id).first()
            if not outlet:
                return JsonResponse({"error": "Invalid outlet"}, status=400)
        else:
            outlet = None
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

    return JsonResponse({
        "success": True,
        "data": {
            "sales": sales,
            "items": list(items),
            "hourly_sales": hourly,
            "table_stats": list(table_stats),
            "categories": categories,
            "waiters": list(waiters),
            "date_filter": date_filter,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "outlets": [{"id": o.id, "name": o.name} for o in outlets]
        }
    }, encoder=DjangoJSONEncoder)


@login_required
@tenant_required
def api_kitchen_dashboard(request):
    if request.user.role not in ["owner", "manager"]:
        return JsonResponse({"error": "Permission denied"}, status=403)

    tenant = request.user.tenant
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
                pass

    outlet_id = request.GET.get("outlet")
    if request.user.role == "owner":
        outlets = Outlet.objects.filter(tenant=tenant)
        if outlet_id:
            outlet = outlets.filter(id=outlet_id).first()
            if not outlet:
                return JsonResponse({"error": "Invalid outlet"}, status=400)
        else:
            outlet = None
    else:
        outlet = request.user.outlet
        outlets = [outlet]

    selected_outlet = outlet

    k_perf = kitchen_performance(tenant, selected_outlet, start_date, end_date)
    k_items = top_kitchen_items(tenant, selected_outlet, start_date, end_date)

    return JsonResponse({
        "success": True,
        "data": {
            "kitchen_performance": k_perf,
            "top_items": list(k_items),
            "date_filter": date_filter,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "outlets": [{"id": o.id, "name": o.name} for o in outlets]
        }
    }, encoder=DjangoJSONEncoder)
