from django.urls import path
from .views import (
    set_default_station,
    setup_wizard,
    onboarding_wizard,
    setup_tables,
    setup_menu,
    setup_kitchen_stations,
    setup_payment_methods,
    setup_staff,
    rename_table,
    aggregator_setup,
    outlet_settings,
    setup_promos,
    promo_create,
    promo_toggle,
    promo_delete,
    toggle_aggregator,
    update_printer_config,
    test_print_station,
    sample_menu,
    checklist_status,
    check_slug_available,
)

urlpatterns = [
    path('', setup_wizard, name='setup_wizard'),
    path('onboard/', onboarding_wizard, name='onboarding_wizard'),
    path('tables/', setup_tables, name='setup_tables'),
    path('menu/', setup_menu, name='setup_menu'),
    path('kitchen-stations/', setup_kitchen_stations, name='setup_kitchen_stations'),
    path('payment-methods/', setup_payment_methods, name='setup_payment_methods'),
    path('staff/', setup_staff, name='setup_staff'),
    path("set-default-station/<int:station_id>/", set_default_station, name="set-default-station"),
    path('tables/<int:table_id>/rename/', rename_table, name='rename_table'),
    path("aggregators/", aggregator_setup, name="setup_aggregators"),
    path("outlet/", outlet_settings, name="outlet_settings"),

    # Promo management
    path("promos/", setup_promos, name="setup_promos"),
    path("promos/create/", promo_create, name="promo_create"),
    path("promos/<int:promo_id>/toggle/", promo_toggle, name="promo_toggle"),
    path("promos/<int:promo_id>/delete/", promo_delete, name="promo_delete"),
    
    # Aggregator quick toggle
    path("aggregators/toggle/", toggle_aggregator, name="toggle_aggregator"),

    # Printer config per station
    path("kitchen-stations/<int:station_id>/printer/", update_printer_config, name="update_printer_config"),
    path("kitchen-stations/<int:station_id>/test-print/", test_print_station, name="test_print_station"),

    # Onboarding helpers
    path("sample-menu/", sample_menu, name="setup_sample_menu"),
    path("checklist/", checklist_status, name="setup_checklist"),
    path("check-slug/", check_slug_available, name="check_slug"),
]