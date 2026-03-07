# accounts/urls.py
from django.urls import path
from .views import login_view, logout_view
from .views import owner_dashboard

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("dashboard/", owner_dashboard, name="dashboard"),
]