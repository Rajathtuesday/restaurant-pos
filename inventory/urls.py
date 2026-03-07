# inventory/urls.py
from django.urls import path
from .views import inventory_board

urlpatterns = [
    path("board/", inventory_board, name="inventory_board"),
]