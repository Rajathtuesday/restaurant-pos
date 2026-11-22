from django.urls import path

from box import views

urlpatterns = [
    path("setup/", views.setup, name="box_setup"),
    path("health/", views.health, name="box_health"),
    path("connect/", views.connect, name="box_connect"),
    path("connect/Rasova.cmd", views.shortcut, name="box_shortcut"),
    path("update-status/", views.update_status, name="box_update_status"),
]
