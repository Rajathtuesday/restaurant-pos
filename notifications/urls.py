# notifications/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("api/notifications/unread/", views.unread_notifications, name="unread-notifications"),
]
