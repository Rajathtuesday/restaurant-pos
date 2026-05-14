# orders/views/__init__.py
# Consolidates all view modules so urls.py import path stays unchanged.
# All names in __all__ are intentional public re-exports consumed by urls.py.

from .billing_core import billing_view, bill_view
from .payment_views import pay_order, split_pay, refund_payment
from .discount_views import apply_discount, make_item_complimentary, apply_item_discount, log_bypass
from .print_views import generate_bill, print_bill_action, print_kot_action, printer_status, download_pdf_bill
from .billing_views import create_order
from .kitchen_views import kitchen_view, kitchen_data, start_preparing, mark_ready, serve_item, send_to_kitchen, send_kitchen_message, bump_kot
from .table_views import table_dashboard, tables_data, mark_table_cleaned, available_tables, merge_tables_view, unmerge_tables_view, transfer_table_view, manage_table_view
from .order_views import running_order_view, running_order_items, running_order_data, approve_items
from .waiter_views import waiter_dashboard, resolve_waiter_call, resolve_kitchen_message
from .order_actions import cancel_order, cancel_item

__all__ = [
    # billing pages
    "billing_view",
    "bill_view",
    # payment
    "pay_order",
    "split_pay",
    "refund_payment",
    # discounts / adjustments
    "apply_discount",
    "make_item_complimentary",
    "apply_item_discount",
    "log_bypass",
    # printing / bill generation
    "generate_bill",
    "print_bill_action",
    "print_kot_action",
    "printer_status",
    "download_pdf_bill",
    # order creation (still in billing_views shim)
    "create_order",
    # kitchen
    "kitchen_view",
    "kitchen_data",
    "start_preparing",
    "mark_ready",
    "serve_item",
    "send_to_kitchen",
    "send_kitchen_message",
    "bump_kot",
    # tables
    "table_dashboard",
    "tables_data",
    "mark_table_cleaned",
    "available_tables",
    "merge_tables_view",
    "unmerge_tables_view",
    "transfer_table_view",
    "manage_table_view",
    # running order
    "running_order_view",
    "running_order_items",
    "running_order_data",
    "approve_items",
    # waiter
    "waiter_dashboard",
    "resolve_waiter_call",
    "resolve_kitchen_message",
    # order actions
    "cancel_order",
    "cancel_item",
]
