from django.urls import path
from .views import (
    setup_wizard,
    setup_tables,
    setup_menu,
    setup_printers,
    setup_payment_methods,
    setup_staff,
)


urlpatterns = [
    path('', setup_wizard, name='setup_wizard'),
    path('tables/', setup_tables, name='setup_tables'),
    path('menu/', setup_menu, name='setup_menu'),
    path('printers/', setup_printers, name='setup_printers'),
    path('payment-methods/', setup_payment_methods, name='setup_payment_methods'),
    path('staff/', setup_staff, name='setup_staff'),
]