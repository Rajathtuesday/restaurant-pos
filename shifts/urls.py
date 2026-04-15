# shifts/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("", views.shift_dashboard, name="shift-dashboard"),
    path("clock-in/", views.clock_in, name="clock-in"),
    path("clock-out/", views.clock_out, name="clock-out"),
    path("<int:shift_id>/tips/", views.update_shift_tips, name="shift-tips"),
    path("sessions/", views.cash_session_list, name="cash-session-list"),
    path("sessions/open/", views.open_cash_session, name="open-cash-session"),
    path("sessions/close/", views.close_cash_session, name="close-cash-session"),
]
