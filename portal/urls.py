from django.urls import path
from . import views

app_name = "portal"

urlpatterns = [
    path("",                            views.portal_home,      name="home"),
    path("create/",                     views.create_restaurant, name="create"),
    path("tenant/<int:tenant_id>/",     views.tenant_config,    name="tenant"),
    path("tenant/<int:tenant_id>/preset/", views.apply_preset,  name="preset"),
]
