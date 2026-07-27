# kitchen/urls.py
from django.urls import path

from . import views

urlpatterns = [
    # Paths unchanged from their old orders/urls.py location -- every
    # template reference confirmed to use {% url %} names, never hardcoded
    # paths, so this move is transparent to the front end.
    path("send-to-kitchen/<int:order_id>/", views.send_to_kitchen, name="send-to-kitchen"),
    path("send-kitchen-message/<int:order_id>/", views.send_kitchen_message, name="send-kitchen-message"),
    path("kitchen/", views.kitchen_view, name="kitchen-view"),
    path("kitchen-data/", views.kitchen_data, name="kitchen-data"),
    path("item-start/<int:item_id>/", views.start_preparing, name="item-start"),
    path("item-ready/<int:item_id>/", views.mark_ready, name="mark-ready"),
    path("bump-kot/<int:kot_id>/", views.bump_kot, name="bump-kot"),
    path("kitchen/clear-all/", views.clear_all_kitchen_items, name="clear-all-kitchen"),
    path("serve-item/<int:item_id>/", views.serve_item, name="serve-item"),

    # Moved from orders/urls.py (Phase 4 of the orders app split) -- was
    # colocated with WaiterCall's routes historically, but only ever
    # operated on KitchenMessage.
    path("resolve-kitchen-message/<int:message_id>/", views.resolve_kitchen_message, name="resolve-kitchen-message"),
]
