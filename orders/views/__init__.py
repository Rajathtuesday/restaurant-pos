# orders/views/__init__.py
# Consolidates all view modules so urls.py import path stays unchanged

from .billing_views import (
    billing_view, create_order, apply_discount, make_item_complimentary, 
    generate_bill, pay_order, bill_view, print_bill_action
)
from .kitchen_views import kitchen_view, kitchen_data, start_preparing, mark_ready, serve_item, send_to_kitchen, send_kitchen_message
from .table_views import table_dashboard, tables_data, mark_table_cleaned, available_tables, merge_tables_view, unmerge_tables_view, transfer_table_view, manage_table_view
from .order_views import running_order_view, running_order_items, running_order_data
from .waiter_views import waiter_dashboard, resolve_waiter_call, resolve_kitchen_message
from .order_actions import cancel_order, cancel_item
