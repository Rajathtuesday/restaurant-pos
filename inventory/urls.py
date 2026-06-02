# inventory/urls.py
from django.urls import path
from . import views
from .central_kitchen_views import (
    central_kitchen_dashboard, create_batch, batch_detail,
    add_batch_item, create_transfers, dispatch_batch,
    receive_page, scan_barcode, confirm_receive,
)
from .requisition_views import (
    requisition_list, create_requisition, auto_generate_requisition,
    requisition_detail, approve_requisition,
    convert_to_batch, convert_to_po, cancel_requisition,
)

urlpatterns = [
    # Inventory board + reports
    path("board/", views.inventory_board, name="inventory_board"),
    path("consumption/", views.consumption_report, name="inventory_consumption"),
    path("variance/", views.variance_report, name="inventory_variance"),
    path("create/", views.create_inventory_item, name="create_inventory_item"),
    path("update/<int:item_id>/", views.update_inventory_item, name="update_inventory_item"),
    path("restock/<int:item_id>/", views.restock_item, name="restock_item"),
    path("wastage/<int:item_id>/", views.log_wastage, name="inventory_log_wastage"),

    # Suppliers
    path("suppliers/", views.supplier_list, name="supplier_list"),
    path("suppliers/create/", views.create_supplier, name="supplier_create"),
    path("suppliers/<int:supplier_id>/delete/", views.delete_supplier, name="supplier_delete"),

    # Purchase Orders
    path("purchase-orders/", views.purchase_order_list, name="purchase_order_list"),
    path("purchase-orders/create/", views.create_purchase_order, name="purchase_order_create"),
    path("purchase-orders/<int:po_id>/order/", views.mark_po_ordered, name="po_mark_ordered"),
    path("purchase-orders/<int:po_id>/receive/", views.receive_purchase_order, name="po_receive"),
    path("purchase-orders/<int:po_id>/cancel/", views.cancel_purchase_order, name="po_cancel"),
    path("purchase-orders/<int:po_id>/print/", views.purchase_order_print, name="po_print"),

    # Legacy alias
    path("purchase-order/", views.purchase_order_view, name="purchase_order_view"),

    # Stock Requisitions
    path("requisitions/",                                  requisition_list,            name="requisition-list"),
    path("requisitions/create/",                           create_requisition,          name="requisition-create"),
    path("requisitions/auto-generate/",                    auto_generate_requisition,   name="requisition-auto-generate"),
    path("requisitions/<int:req_id>/",                     requisition_detail,          name="requisition-detail"),
    path("requisitions/<int:req_id>/approve/",             approve_requisition,         name="requisition-approve"),
    path("requisitions/<int:req_id>/to-batch/",            convert_to_batch,            name="requisition-to-batch"),
    path("requisitions/<int:req_id>/to-po/",               convert_to_po,               name="requisition-to-po"),
    path("requisitions/<int:req_id>/cancel/",              cancel_requisition,          name="requisition-cancel"),

    # Central Kitchen
    path("central-kitchen/",                              central_kitchen_dashboard, name="central-kitchen"),
    path("central-kitchen/batch/create/",                 create_batch,              name="ck-create-batch"),
    path("central-kitchen/batch/<int:batch_id>/",         batch_detail,              name="ck-batch-detail"),
    path("central-kitchen/batch/<int:batch_id>/add-item/",add_batch_item,            name="ck-add-item"),
    path("central-kitchen/batch/<int:batch_id>/transfer/",create_transfers,          name="ck-create-transfers"),
    path("central-kitchen/batch/<int:batch_id>/dispatch/",dispatch_batch,            name="ck-dispatch"),
    path("central-kitchen/receive/",                      receive_page,              name="ck-receive"),
    path("central-kitchen/scan/",                         scan_barcode,              name="ck-scan"),
    path("central-kitchen/receive/<int:transfer_id>/confirm/", confirm_receive,      name="ck-confirm-receive"),
]