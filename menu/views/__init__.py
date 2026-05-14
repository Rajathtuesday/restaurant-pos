"""
menu.views package — re-exports everything so urls.py needs no changes.

Split by concern:
  customer_views   — QR menu, digital menu, waiter call (no login required)
  management_views — menu management page
  item_views       — item CRUD, toggle, platform, station, recipe
  category_views   — category CRUD
  modifier_views   — modifier groups, modifiers, item linking
  gst_views        — GST rate management
  ai_views         — AI photo importer, multi-outlet sync
"""
from .customer_views import menu_view, call_waiter, digital_menu
from .management_views import menu_management
from .item_views import (
    create_menu_item, update_menu_item, delete_menu_item,
    toggle_item, toggle_platform_availability,
    update_price, update_station, add_recipe,
)
from .category_views import create_category, delete_category
from .modifier_views import (
    modifier_management, menu_item_modifiers,
    create_modifier_group, delete_modifier_group,
    add_modifier, delete_modifier,
    link_modifier_group, unlink_modifier_group,
)
from .gst_views import gst_management, update_item_gst, update_category_gst
from .ai_views import ai_menu_importer, sync_menu_to_outlets

__all__ = [
    "menu_view", "call_waiter", "digital_menu",
    "menu_management",
    "create_menu_item", "update_menu_item", "delete_menu_item",
    "toggle_item", "toggle_platform_availability",
    "update_price", "update_station", "add_recipe",
    "create_category", "delete_category",
    "modifier_management", "menu_item_modifiers",
    "create_modifier_group", "delete_modifier_group",
    "add_modifier", "delete_modifier",
    "link_modifier_group", "unlink_modifier_group",
    "gst_management", "update_item_gst", "update_category_gst",
    "ai_menu_importer", "sync_menu_to_outlets",
]
