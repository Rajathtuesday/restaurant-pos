# shifts/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("", views.shift_dashboard, name="shift-dashboard"),
    path("clock-in/", views.clock_in, name="clock-in"),
    path("clock-out/", views.clock_out, name="clock-out"),
    path("<int:shift_id>/tips/", views.update_shift_tips, name="shift-tips"),
]
