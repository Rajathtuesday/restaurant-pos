# orders/views/__init__.py
# Consolidates all view modules so urls.py import path stays unchanged.
# All names in __all__ are intentional public re-exports consumed by urls.py.

from .billing_core import billing_view, bill_view
from .payment_views import pay_order, split_pay, refund_payment
from .discount_views import apply_discount, make_item_complimentary, apply_item_discount, log_bypass
from .print_views import generate_bill, print_bill_action, print_kot_action, print_split_bill, qz_receipt_data, printer_status, download_pdf_bill, thermal_receipt_view
from .billing_views import create_order
# kitchen_views moved to kitchen/views.py (Phase 3 of the orders app split)
from .table_views import table_dashboard, tables_data, mark_table_cleaned, available_tables, transfer_table_view, manage_table_view
# merge_tables_view/unmerge_tables_view moved to tablemerge/views.py (Phase 5 of the split).
from .order_views import running_order_view, running_order_items, running_order_data, approve_items
# waiter_dashboard/resolve_waiter_call moved to waiter/views.py, resolve_kitchen_message
# to kitchen/views.py (Phase 4 of the orders app split).
from .order_actions import cancel_order, cancel_item
from .history_views import order_history_view, order_detail_api, export_orders_csv

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
    "print_split_bill",
    "qz_receipt_data",
    "printer_status",
    "download_pdf_bill",
    "thermal_receipt_view",
    # order creation (still in billing_views shim)
    "create_order",
    # kitchen -- moved to kitchen/views.py (Phase 3 of the orders app split)
    # tables
    "table_dashboard",
    "tables_data",
    "mark_table_cleaned",
    "available_tables",
    # merge_tables_view/unmerge_tables_view moved to tablemerge/views.py (Phase 5).
    "transfer_table_view",
    "manage_table_view",
    # running order
    "running_order_view",
    "running_order_items",
    "running_order_data",
    "approve_items",
    # waiter_dashboard/resolve_waiter_call moved to waiter/views.py,
    # resolve_kitchen_message to kitchen/views.py (Phase 4 of the split).
    # order actions
    "cancel_order",
    "cancel_item",
    # history
    "order_history_view",
    "order_detail_api",
    "export_orders_csv",
]
