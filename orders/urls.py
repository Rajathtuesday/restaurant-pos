# orders/urls.py
from django.urls import path

from .views import *

urlpatterns = [

    path("billing/", billing_view),

    path("create-order/", create_order),

    path("send-to-kitchen/<int:order_id>/", send_to_kitchen),

    path("kitchen/", kitchen_view),
    path("kitchen-data/", kitchen_data),

    path("item-start/<int:item_id>/", start_preparing),
    path("item-ready/<int:item_id>/", mark_ready),

    path("serve-item/<int:item_id>/", serve_item),

    path("bill/<int:order_id>/", bill_view),
    path("pay/<int:order_id>/", pay_order),

    path("tables/", table_dashboard),
    path("tables-data/", tables_data),

    path("clean-table/<int:table_id>/", mark_table_cleaned),
    
    
    path("running-order-items/", running_order_items),
    path("order/<int:order_id>/", running_order_view),
    path("order-data/<int:order_id>/", running_order_data),
    

]