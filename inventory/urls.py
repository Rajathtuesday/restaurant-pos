# inventory/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Inventory board
    path("board/", views.inventory_board, name="inventory_board"),
    path("create/", views.create_inventory_item, name="create_inventory_item"),
    path("update/<int:item_id>/", views.update_inventory_item, name="update_inventory_item"),
    path("restock/<int:item_id>/", views.restock_item, name="restock_item"),

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
]