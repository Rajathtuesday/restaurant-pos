# orders/urls.py
from django.urls import path

from .views.order_actions import cancel_order, cancel_item, toggle_parcel
from .views.billing_views import refund_payment, apply_item_discount, log_bypass, split_pay, download_pdf_bill
from .views.refund_views import approve_refund_view, reject_refund_view, pending_refunds_view
from .views.public_views import public_bill, submit_feedback
from .views.razorpay_views import create_razorpay_qr, razorpay_qr_status, razorpay_webhook
from .api import api_tables, api_active_orders, api_ingest_order, notification_api



from .views import (
    apply_discount,
    available_tables,
    billing_view,
    manage_table_view,
    create_order,
    make_item_complimentary,
    bill_view,
    pay_order,
    table_dashboard,
    tables_data,
    mark_table_cleaned,
    running_order_items,
    running_order_view,
    running_order_data,
    generate_bill,
    merge_tables_view,
    transfer_table_view,
    unmerge_tables_view,
    print_bill_action,
    print_kot_action,
    print_split_bill,
    qz_receipt_data,
    approve_items,
    printer_status,
    thermal_receipt_view,
    order_history_view,
    order_detail_api,
    export_orders_csv,
)


urlpatterns = [

    # Token System routes moved to tokens/urls.py (Phase 2 of the orders app
    # split) -- same exact paths.

    path("billing/", billing_view ,name="billing-view"),

    path("create-order/", create_order, name="create-order"),

    # Kitchen routes (send-to-kitchen, kitchen-view, kitchen-data, item-start,
    # mark-ready, bump-kot, serve-item, send-kitchen-message, resolve-kitchen-message)
    # moved to kitchen/urls.py (Phases 3-4 of the orders app split) -- same exact paths.
    # waiter-dashboard/resolve-waiter moved to waiter/urls.py (Phase 4).

    path("bill/<int:order_id>/", bill_view, name="bill-view"),
    path("pay/<int:order_id>/", pay_order, name="pay-order"),
    path("print-bill/<int:order_id>/", print_bill_action, name="print-bill"),
    path("print-split-bill/<int:order_id>/", print_split_bill, name="print-split-bill"),
    path("qz-data/<int:order_id>/", qz_receipt_data, name="qz-receipt-data"),
    path("print-kot/<int:kot_id>/", print_kot_action, name="print-kot"),
    path("printer-status/", printer_status, name="printer-status"),
    path("download-pdf/<int:order_id>/", download_pdf_bill, name="download-pdf"),
    path("thermal-receipt/<int:order_id>/", thermal_receipt_view, name="thermal-receipt"),

    # Order History
    path("history/",                      order_history_view, name="order-history"),
    path("history/<int:order_id>/detail/", order_detail_api,   name="order-detail-api"),
    path("history/export/",               export_orders_csv,  name="export-orders-csv"),

    path("tables/", table_dashboard, name="table-dashboard"),
    path("tables-data/", tables_data ,name="tables-data"),
    path("manage-table/", manage_table_view, name="manage-table"),

    path("clean-table/<int:table_id>/", mark_table_cleaned ,name="clean-table"),

    path("cancel-order/<int:order_id>/", cancel_order, name="cancel-order"),
    path("cancel-item/<int:item_id>/", cancel_item, name="cancel-item"),
    path("toggle-parcel/<int:order_id>/", toggle_parcel, name="toggle-parcel"),

    path("running-order-items/", running_order_items ,name="running-order-items"),
    path("order/<int:order_id>/", running_order_view, name="running-order"),
    path("order-data/<int:order_id>/", running_order_data, name="running-order-data"),

    path("generate-bill/<int:order_id>/", generate_bill, name="generate-bill"),
    path("approve-items/<int:order_id>/", approve_items, name="approve-items"),

    path("apply-discount/<int:order_id>/", apply_discount, name="apply-discount"),
    
    path("complimentary-item/<int:item_id>/", make_item_complimentary, name="make-complimentary"),
    
    
    path("merge-tables/", merge_tables_view ,name="merge-tables"),
    path("unmerge-tables/<int:primary_id>/",unmerge_tables_view ,name="unmerge-tables"),
    
    path("transfer-table/", transfer_table_view ,name="transfer-table"),
    
    path("available-tables/", available_tables ,name="available-tables"),

    path("refund/<int:payment_id>/", refund_payment, name="refund-payment"),
    
    path("api/notifications/", notification_api, name="notification-api"),
    path("api/notifications/unread/", notification_api, name="notification-api-unread"),
    path("refunds/pending/", pending_refunds_view, name="pending-refunds"),
    path("refund/approve/<int:refund_id>/", approve_refund_view, name="approve-refund"),
    path("refund/reject/<int:refund_id>/", reject_refund_view, name="reject-refund"),
    path("item-discount/<int:item_id>/", apply_item_discount, name="item-discount"),
    path("log-bypass/<int:order_id>/", log_bypass, name="log-bypass"),
    path("split-pay/<int:order_id>/", split_pay, name="split-pay"),

    # API Routes for Headless/Mobile Clients
    path("api/tables/", api_tables, name="api-tables"),
    path("api/active/", api_active_orders, name="api-active-orders"),
    path("api/aggregator/webhook/", api_ingest_order, name="api-ingest-order"),

    # Promo routes moved to promos/urls.py (Phase 0 of the orders app split)

    # Public, login-free bill link (WhatsApp receipt)
    path("bill/public/<str:signed_token>/", public_bill, name="public-bill"),
    path("bill/public/<str:signed_token>/feedback/", submit_feedback, name="public-feedback"),

    # Razorpay — UPI QR (BYO-keys)
    path("razorpay/create-qr/<int:order_id>/", create_razorpay_qr, name="razorpay-create-qr"),
    path("razorpay/qr-status/<str:qr_code_id>/", razorpay_qr_status, name="razorpay-qr-status"),
    path("api/razorpay/webhook/", razorpay_webhook, name="razorpay-webhook"),

    # Print queue routes moved to printing/urls.py (Phase 1 of the orders app
    # split) -- same exact paths, real agents poll these URLs directly.
]
