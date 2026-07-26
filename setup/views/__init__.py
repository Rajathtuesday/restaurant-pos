# setup/views/__init__.py
# Re-exports all view functions so that setup/urls.py imports remain unchanged.

from .core_views import (
    setup_wizard,
    setup_tables,
    setup_menu,
    setup_kitchen_stations,
    update_printer_config,
    test_print_station,
    setup_payment_methods,
    setup_staff,
    reset_staff_password,
    toggle_staff_active,
    edit_staff_role,
    edit_staff_outlet,
    edit_pay_rate,
    set_default_station,
    delete_station,
    set_print_mode,
    rename_table,
    outlet_settings,
    printer_setup,
    printer_test_print,
    setup_qr_codes,
)

from .promo_views import (
    setup_promos,
    promo_create,
    promo_toggle,
    promo_delete,
)

from .onboarding_views import (
    onboarding_wizard,
    sample_menu,
    checklist_status,
    check_slug_available,
)

from .aggregator_views import (
    aggregator_setup,
    toggle_aggregator,
)

from .report_subscription_views import (
    report_subscriptions,
    report_subscription_create,
    report_subscription_toggle,
    report_subscription_delete,
)

__all__ = [
    # core
    "setup_wizard",
    "setup_tables",
    "setup_menu",
    "setup_kitchen_stations",
    "update_printer_config",
    "test_print_station",
    "setup_payment_methods",
    "setup_staff",
    "reset_staff_password",
    "toggle_staff_active",
    "edit_staff_role",
    "edit_staff_outlet",
    "edit_pay_rate",
    "set_default_station",
    "delete_station",
    "rename_table",
    "outlet_settings",
    "printer_setup",
    "printer_test_print",
    "setup_qr_codes",
    # promos
    "setup_promos",
    "promo_create",
    "promo_toggle",
    "promo_delete",
    # onboarding
    "onboarding_wizard",
    "sample_menu",
    "checklist_status",
    "check_slug_available",
    # aggregators
    "aggregator_setup",
    "toggle_aggregator",
    # report subscriptions
    "report_subscriptions",
    "report_subscription_create",
    "report_subscription_toggle",
    "report_subscription_delete",
]
