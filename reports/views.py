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
from django.utils import timezone
from datetime import timedelta
import csv
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse, JsonResponse

from reports.services.kitchen_reports import kitchen_performance, top_kitchen_items

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

    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="pos_report_{start_date}_{end_date}.csv"'
        writer = csv.writer(response)
        
        writer.writerow(['SUMMARY'])
        writer.writerow(['Revenue', f"Rs {sales.get('total_sales', 0)}"])
        writer.writerow(['Orders', sales.get('orders', 0)])
        writer.writerow([])
        
        writer.writerow(['PAYMENT METHODS'])
        writer.writerow(['Method', 'Total Amount'])
        for pm in sales.get('payments', []):
            writer.writerow([pm.get('method', 'Unknown'), pm.get('total', 0)])
        writer.writerow([])
        
        writer.writerow(['TOP ITEMS'])
        writer.writerow(['Item Name', 'Quantity Sold', 'Revenue generated'])
        for item in items:
            writer.writerow([item['menu_item__name'], item['total_qty'], item['total_rev']])
        writer.writerow([])
        
        writer.writerow(['CATEGORY SALES'])
        writer.writerow(['Category', 'Revenue'])
        for cat, rev in categories:
            writer.writerow([cat, rev])
            
        return response

    if request.GET.get('format') == 'json':
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
                "end_date": str(end_date)
            }
        }, encoder=DjangoJSONEncoder)

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

@login_required
def kitchen_dashboard(request):
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
                pass

    # outlet selection
    outlet_id = request.GET.get("outlet")

    if request.user.role == "owner":
        outlets = Outlet.objects.filter(tenant=tenant)
        if outlet_id:
            outlet = outlets.filter(id=outlet_id).first()
            if not outlet:
                return HttpResponseForbidden("Invalid outlet")
        else:
            outlet = None
    else:
        outlet = request.user.outlet
        outlets = [outlet]

    selected_outlet = outlet

    k_perf = kitchen_performance(tenant, selected_outlet, start_date, end_date)
    k_items = top_kitchen_items(tenant, selected_outlet, start_date, end_date)

    if request.GET.get('format') == 'json':
        return JsonResponse({
            "success": True,
            "data": {
                "kitchen_performance": k_perf,
                "top_items": list(k_items),
                "date_filter": date_filter,
                "start_date": str(start_date),
                "end_date": str(end_date)
            }
        }, encoder=DjangoJSONEncoder)

    return render(request, "reports/kitchen_dashboard.html", {
        "k_perf": k_perf,
        "k_items": k_items,
        "outlets": outlets,
        "current_outlet": outlet,
        "date_filter": date_filter,
        "start_date": start_date,
        "end_date": end_date
    })