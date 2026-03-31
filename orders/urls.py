# orders/urls.py
from django.urls import path

from orders.services.table_transfer_service import transfer_table

from .views.order_actions import cancel_order, cancel_item



from .views import (
    apply_discount,
    available_tables,
    billing_view,
    create_order,
    make_item_complimentary,
    send_to_kitchen,
    kitchen_view,
    kitchen_data,
    start_preparing,
    mark_ready,
    serve_item,
    send_kitchen_message,
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
    waiter_dashboard,
    resolve_waiter_call,
    resolve_kitchen_message
)

urlpatterns = [

    path("billing/", billing_view ,name="billing-view"),

    path("create-order/", create_order, name="create-order"),

    path("send-to-kitchen/<int:order_id>/", send_to_kitchen , name="send-to-kitchen"),
    path("send-kitchen-message/<int:order_id>/", send_kitchen_message, name="send-kitchen-message"),

    path("kitchen/", kitchen_view ,name="kitchen-view"),
    path("kitchen-data/", kitchen_data, name="kitchen-data"),

    path("item-start/<int:item_id>/", start_preparing),
    path("item-ready/<int:item_id>/", mark_ready, name="mark-ready"),

    path("serve-item/<int:item_id>/", serve_item, name="serve-item"),

    path("bill/<int:order_id>/", bill_view, name="bill-view"),
    path("pay/<int:order_id>/", pay_order, name="pay-order"),

    path("tables/", table_dashboard ,name="table-dashboard"),
    path("tables-data/", tables_data ,name="tables-data"),

    path("clean-table/<int:table_id>/", mark_table_cleaned ,name="clean-table"),

    path("cancel-order/<int:order_id>/", cancel_order, name="cancel-order"),
    path("cancel-item/<int:item_id>/", cancel_item, name="cancel-item"),

    path("running-order-items/", running_order_items ,name="running-order-items"),
    path("order/<int:order_id>/", running_order_view, name="running-order"),
    path("order-data/<int:order_id>/", running_order_data, name="running-order-data"),

    path("generate-bill/<int:table_id>/", generate_bill, name="generate-bill"),

    path("apply-discount/<int:order_id>/", apply_discount, name="apply-discount"),
    
    path("complimentary-item/<int:item_id>/", make_item_complimentary, name="make-complimentary"),
    
    
    path("merge-tables/", merge_tables_view ,name="merge-tables"),
    path("unmerge-tables/<int:primary_id>/",unmerge_tables_view ,name="unmerge-tables"),
    
    path("transfer-table/", transfer_table_view ,name="transfer-table"),
    
    path("available-tables/", available_tables ,name="available-tables"),

    path("waiter-dashboard/", waiter_dashboard, name="waiter-calls"),
    path("resolve-waiter/<int:call_id>/", resolve_waiter_call, name="resolve-waiter"),
    path("resolve-kitchen-message/<int:message_id>/", resolve_kitchen_message, name="resolve-kitchen-message"),
]