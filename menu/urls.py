#menu/urls.py
from django.urls import path
from .views import (
    menu_item_modifiers,
    menu_view,
    call_waiter,
    menu_management,
    create_category,
    create_menu_item,
    add_recipe,
    delete_menu_item,
    update_price,
    toggle_item,
    update_station,
    ai_menu_importer,
    delete_category,
)

urlpatterns = [

    # owner menu panel
    path("", menu_management, name="menu_management"),

    # menu APIs
    path("create-category/", create_category, name="create_category"),
    path("create-item/", create_menu_item, name="create_menu_item"),
    path("delete-category/<int:category_id>/", delete_category, name="delete_category"),
    path("add-recipe/", add_recipe, name="add_recipe"),
    path("ai-import/", ai_menu_importer, name="ai_menu_importer"),

    path("delete-item/<int:item_id>/", delete_menu_item, name="delete_menu_item"),
    path("update-price/<int:item_id>/", update_price, name="update_price"),
    path("toggle-item/<int:item_id>/", toggle_item, name="toggle_item"),
    
    path("update-station/<int:item_id>/", update_station, name="update_station"),

    # modifier API
    path("item-modifiers/<int:item_id>/", menu_item_modifiers, name="menu_item_modifiers"),

    # waiter
    path("call-waiter/<uuid:qr_token>/", call_waiter, name="call_waiter"),

    # qr menu (KEEP LAST)
    path("<uuid:qr_token>/", menu_view, name="menu_view"),
]