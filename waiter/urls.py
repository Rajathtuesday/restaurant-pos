# waiter/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # Paths unchanged from their old orders/urls.py location -- every
    # template reference confirmed to use {% url %} names, never hardcoded
    # paths, so this move is transparent to the front end.
    path("waiter-dashboard/", views.waiter_dashboard, name="waiter-calls"),
    path("resolve-waiter/<int:call_id>/", views.resolve_waiter_call, name="resolve-waiter"),
]
