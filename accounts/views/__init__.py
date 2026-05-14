"""
accounts.views package — re-exports everything so urls.py needs no changes.

  auth_views       — login_view, logout_view
  dashboard_views  — owner_dashboard, dashboard_metrics_json, sales_dashboard
  feature_views    — feature_flags_view, toggle_feature_flag
  superuser_views  — superuser_panel, create_restaurant, tenant_config, apply_preset
"""
from .auth_views import login_view, logout_view
from .dashboard_views import owner_dashboard, dashboard_metrics_json, sales_dashboard
from .feature_views import feature_flags_view, toggle_feature_flag
from .superuser_views import (
    superuser_panel, create_restaurant, tenant_config, apply_preset,
)

__all__ = [
    "login_view", "logout_view",
    "owner_dashboard", "dashboard_metrics_json", "sales_dashboard",
    "feature_flags_view", "toggle_feature_flag",
    "superuser_panel", "create_restaurant", "tenant_config", "apply_preset",
]
