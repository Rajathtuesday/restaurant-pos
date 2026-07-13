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
    toggle_platform_availability,
    update_station,
    update_menu_item,
    ai_menu_importer,
    ai_import_status,
    delete_category,
    digital_menu,
    order_status,
    modifier_management,
    create_modifier_group,
    delete_modifier_group,
    add_modifier,
    delete_modifier,
    link_modifier_group,
    unlink_modifier_group,
    add_modifier_recipe,
    delete_modifier_recipe,
    modifier_inventory_links,
    gst_management,
    update_item_gst,
    update_category_gst,
    sync_menu_to_outlets,
)
from .api import api_categories, api_items

urlpatterns = [

    # owner menu panel
    path("", menu_management, name="menu_management"),

    # menu APIs
    path("create-category/", create_category, name="create_category"),
    path("create-item/", create_menu_item, name="create_menu_item"),
    path("delete-category/<int:category_id>/", delete_category, name="delete_category"),
    path("add-recipe/", add_recipe, name="add_recipe"),
    path("ai-import/", ai_menu_importer, name="ai_menu_importer"),
    path("ai-import-status/<str:task_id>/", ai_import_status, name="ai_import_status"),

    path("delete-item/<int:item_id>/", delete_menu_item, name="delete_menu_item"),
    path("update-price/<int:item_id>/", update_price, name="update_price"),
    path("toggle-item/<int:item_id>/", toggle_item, name="toggle_item"),
    path("toggle-platform/<int:item_id>/", toggle_platform_availability, name="toggle_platform"),
    path("digital-menu/", digital_menu, name="digital_menu"),
    path("qr/", digital_menu, name="menu_qr"),
    path("order-status/<int:order_id>/", order_status, name="order_status"),
    path("sync-outlets/", sync_menu_to_outlets, name="sync_menu_to_outlets"),
    
    path("update-station/<int:item_id>/", update_station, name="update_station"),
    path("update-item/<int:item_id>/", update_menu_item, name="update_menu_item"),

    # modifier management
    path("modifiers/", modifier_management, name="modifier_management"),
    path("modifiers/create-group/", create_modifier_group, name="create_modifier_group"),
    path("modifiers/delete-group/<int:group_id>/", delete_modifier_group, name="delete_modifier_group"),
    path("modifiers/add-modifier/", add_modifier, name="add_modifier"),
    path("modifiers/delete-modifier/<int:modifier_id>/", delete_modifier, name="delete_modifier"),
    path("modifiers/link/", link_modifier_group, name="link_modifier_group"),
    path("modifiers/unlink/", unlink_modifier_group, name="unlink_modifier_group"),
    path("modifiers/<int:modifier_id>/inventory/", modifier_inventory_links, name="modifier_inventory_links"),
    path("modifiers/<int:modifier_id>/inventory/add/", add_modifier_recipe, name="add_modifier_recipe"),
    path("modifiers/<int:modifier_id>/inventory/delete/", delete_modifier_recipe, name="delete_modifier_recipe"),

    # GST management
    path("gst/", gst_management, name="gst_management"),
    path("gst/item/<int:item_id>/", update_item_gst, name="update_item_gst"),
    path("gst/category/<int:category_id>/", update_category_gst, name="update_category_gst"),

    # modifier API
    path("item-modifiers/<int:item_id>/", menu_item_modifiers, name="menu_item_modifiers"),

    # waiter
    path("call-waiter/<uuid:qr_token>/", call_waiter, name="call_waiter"),

    # qr menu (KEEP LAST)
    path("<uuid:qr_token>/", menu_view, name="menu_view"),
    # API Routes for Headless/Mobile Clients
    path('api/categories/', api_categories, name='api_categories'),
    path('api/items/', api_items, name='api_items'),
]