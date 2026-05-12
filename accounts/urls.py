# accounts/urls.py
from django.urls import path
from .views import login_view, logout_view
from .views import owner_dashboard, sales_dashboard, feature_flags_view, toggle_feature_flag

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("dashboard/", owner_dashboard, name="dashboard"),
    path("sales/", sales_dashboard, name="sales_dashboard"),
    path("settings/features/", feature_flags_view, name="feature_flags"),
    path("settings/features/toggle/", toggle_feature_flag, name="toggle_feature_flag"),
]