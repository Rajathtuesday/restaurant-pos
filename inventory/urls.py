# inventory/urls.py
from django.urls import path
from .views import inventory_board, restock_item

urlpatterns = [
    path("board/", inventory_board, name="inventory_board"),
    path("restock/<int:item_id>/", restock_item, name="restock_item"),
]