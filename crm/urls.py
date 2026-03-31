# crm/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("", views.crm_dashboard, name="crm-dashboard"),
    path("guest/<int:guest_id>/", views.guest_profile, name="guest-profile"),
    path("lookup/", views.guest_lookup, name="guest-lookup"),
    path("link/<int:order_id>/", views.link_guest_to_order, name="link-guest"),
]
