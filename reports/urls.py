# reports/urls.py

from django.urls import path
from .views import dashboard, kitchen_dashboard, export_reports, inventory_report, inspection_report
from .api import api_dashboard, api_kitchen_dashboard

urlpatterns = [
    path("dashboard/", dashboard, name="dashboard"),
    path("kitchen/", kitchen_dashboard, name="kitchen_dashboard"),
    path("inventory/", inventory_report, name="inventory_report"),
    path("inspect/", inspection_report, name="inspection_report"),
    path("export/", export_reports, name="export_reports"),
    
    # API Routes for Headless/Mobile Clients
    path("api/dashboard/", api_dashboard, name="api_dashboard"),
    path("api/kitchen/", api_kitchen_dashboard, name="api_kitchen_dashboard"),
]