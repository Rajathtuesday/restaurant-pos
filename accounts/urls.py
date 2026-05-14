# accounts/urls.py
from django.urls import path
from .views import (
    login_view, logout_view,
    owner_dashboard, sales_dashboard, feature_flags_view,
    toggle_feature_flag, dashboard_metrics_json,
    superuser_panel, create_restaurant, tenant_config, apply_preset,
)

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("dashboard/", owner_dashboard, name="dashboard"),
    path("dashboard/metrics.json", dashboard_metrics_json, name="dashboard-metrics-json"),
    path("sales/", sales_dashboard, name="sales_dashboard"),
    path("settings/features/", feature_flags_view, name="feature_flags"),
    path("settings/features/toggle/", toggle_feature_flag, name="toggle_feature_flag"),
    # Superuser control panel
    path("superuser/", superuser_panel, name="superuser_panel"),
    path("superuser/create/", create_restaurant, name="superuser_create"),
    path("superuser/tenant/<int:tenant_id>/", tenant_config, name="superuser_tenant"),
    path("superuser/tenant/<int:tenant_id>/preset/", apply_preset, name="superuser_preset"),
]