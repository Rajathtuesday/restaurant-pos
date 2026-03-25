from django.urls import path
from .views import (
    setup_wizard,
    setup_tables,
    setup_menu,
    setup_kitchen_stations,
    setup_payment_methods,
    setup_staff,
)


urlpatterns = [
    path('', setup_wizard, name='setup_wizard'),
    path('tables/', setup_tables, name='setup_tables'),
    path('menu/', setup_menu, name='setup_menu'),
    path('kitchen-stations/', setup_kitchen_stations, name='setup_kitchen_stations'),
    path('payment-methods/', setup_payment_methods, name='setup_payment_methods'),
    path('staff/', setup_staff, name='setup_staff'),
    path("set-default-station/<int:station_id>/", setup_kitchen_stations, name="set-default-station"),
]