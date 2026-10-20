from django.urls import path

from box import views

urlpatterns = [
    path("setup/", views.setup, name="box_setup"),
    path("health/", views.health, name="box_health"),
]
