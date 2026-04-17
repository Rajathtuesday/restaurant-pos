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
    # Allow Owners, Managers, Agents, and Superusers
    if request.user.role not in ["owner", "manager", "agent"] and not request.user.is_superuser:
        return HttpResponseForbidden()

    # Determine which tenant we are viewing
    tenant = request.user.tenant
    
    # If superuser or agent, they can specify a tenant_id to view
    target_tenant_id = request.GET.get("tenant_id")
    if target_tenant_id and (request.user.role == "agent" or request.user.is_superuser):
        from tenants.models import Tenant
        if request.user.is_superuser:
            tenant = Tenant.objects.get(id=target_tenant_id)
        else:
            # Agent can ONLY see tenants assigned to them
            tenant = Tenant.objects.filter(id=target_tenant_id, sales_agent=request.user).first()
            if not tenant:
                return HttpResponseForbidden("You are not the sales agent for this restaurant.")
    
    if not tenant:
        return HttpResponseForbidden("No tenant context found.")

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
        
        # Privacy check for CSV
        if not (request.user.role == 'agent' and not request.user.is_superuser):
            writer.writerow(['TOP ITEMS'])
            writer.writerow(['Item Name', 'Quantity Sold', 'Revenue generated'])
            for item in items:
                writer.writerow([item.get('menu_item__name', 'Item'), item.get('total', 0), item.get('total_rev', 0)])
            writer.writerow([])
            
            writer.writerow(['CATEGORY SALES'])
            writer.writerow(['Category', 'Revenue'])
            for c in categories:
                writer.writerow([c.get('menu_item__category__name', 'Misc'), c.get('revenue', 0)])
        
        return response

    if request.GET.get('format') == 'json':
        # Privacy check for JSON
        is_limited = (request.user.role == 'agent' and not request.user.is_superuser)
        
        data = {
            "sales": sales,
            "hourly_sales": hourly,
            "date_filter": date_filter,
            "start_date": str(start_date),
            "end_date": str(end_date)
        }
        
        if not is_limited:
            data.update({
                "items": list(items),
                "table_stats": list(table_stats),
                "categories": categories,
                "waiters": list(waiters),
            })
            
        return JsonResponse({"success": True, "data": data}, encoder=DjangoJSONEncoder)

    is_limited_view = (request.user.role == "agent" and not request.user.is_superuser)

    return render(request, "reports/dashboard.html", {
        "sales": sales,
        "items": items if not is_limited_view else [],
        "hourly_sales": hourly,
        "table_stats": table_stats if not is_limited_view else [],
        "categories": categories if not is_limited_view else [],
        "waiters": waiters if not is_limited_view else [],
        "outlets": outlets,
        "current_outlet": outlet,
        "date_filter": date_filter,
        "start_date": start_date,
        "end_date": end_date,
        "is_limited_view": is_limited_view
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