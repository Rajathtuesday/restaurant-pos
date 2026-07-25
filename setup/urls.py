from django.urls import path
from .views import (
    set_default_station,
    delete_station,
    set_print_mode,
    setup_wizard,
    onboarding_wizard,
    setup_tables,
    setup_menu,
    setup_kitchen_stations,
    setup_payment_methods,
    setup_staff,
    reset_staff_password,
    toggle_staff_active,
    edit_staff_role,
    rename_table,
    aggregator_setup,
    outlet_settings,
    printer_setup,
    printer_test_print,
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
    setup_qr_codes,
)

urlpatterns = [
    path('', setup_wizard, name='setup_wizard'),
    path('onboard/', onboarding_wizard, name='onboarding_wizard'),
    path('tables/', setup_tables, name='setup_tables'),
    path('qr-codes/', setup_qr_codes, name='setup_qr_codes'),
    path('menu/', setup_menu, name='setup_menu'),
    path('kitchen-stations/', setup_kitchen_stations, name='setup_kitchen_stations'),
    path('payment-methods/', setup_payment_methods, name='setup_payment_methods'),
    path('staff/', setup_staff, name='setup_staff'),
    path('staff/<int:user_id>/reset-password/', reset_staff_password, name='reset_staff_password'),
    path('staff/<int:user_id>/toggle-active/', toggle_staff_active, name='toggle_staff_active'),
    path('staff/<int:user_id>/edit-role/', edit_staff_role, name='edit_staff_role'),
    path("set-default-station/<int:station_id>/", set_default_station, name="set-default-station"),
    path('tables/<int:table_id>/rename/', rename_table, name='rename_table'),
    path("aggregators/", aggregator_setup, name="setup_aggregators"),
    path("outlet/", outlet_settings, name="outlet_settings"),
    path("printer/", printer_setup, name="printer_setup"),
    path("printer/test/", printer_test_print, name="printer_test_print"),

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
    path("kitchen-stations/<int:station_id>/delete/", delete_station, name="delete_station"),
    path("kitchen-stations/set-mode/<str:mode>/", set_print_mode, name="set_print_mode"),

    # Onboarding helpers
    path("sample-menu/", sample_menu, name="setup_sample_menu"),
    path("checklist/", checklist_status, name="setup_checklist"),
    path("check-slug/", check_slug_available, name="check_slug"),
]