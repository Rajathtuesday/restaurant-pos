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
    set_default_station,
    rename_table,
    outlet_settings,
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
    "set_default_station",
    "rename_table",
    "outlet_settings",
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
]
