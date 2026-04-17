# agency/urls.py
from django.urls import path
from .views import agency_performance_dashboard, agency_stats_api

urlpatterns = [
    path('dashboard/', agency_performance_dashboard, name='agency_dashboard'),
    path('api/stats/', agency_stats_api, name='agency_stats_api'),
]
