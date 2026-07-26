# reports/urls.py

from django.urls import path
from .views import dashboard, kitchen_dashboard, export_reports, inventory_report, inspection_report, menu_engineering_report_view, labor_report_view, audit_report_view, crm_analytics_report_view
from .api import api_dashboard, api_kitchen_dashboard

urlpatterns = [
    path("dashboard/", dashboard, name="dashboard"),
    path("kitchen/", kitchen_dashboard, name="kitchen_dashboard"),
    path("inventory/", inventory_report, name="inventory_report"),
    path("inspect/", inspection_report, name="inspection_report"),
    path("menu-engineering/", menu_engineering_report_view, name="menu_engineering"),
    path("labor/", labor_report_view, name="labor_report"),
    path("audit/", audit_report_view, name="audit_report"),
    path("crm-analytics/", crm_analytics_report_view, name="crm_analytics_report"),
    path("export/", export_reports, name="export_reports"),
    
    # API Routes for Headless/Mobile Clients
    path("api/dashboard/", api_dashboard, name="api_dashboard"),
    path("api/kitchen/", api_kitchen_dashboard, name="api_kitchen_dashboard"),
]