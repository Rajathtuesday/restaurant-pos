"""
The fast set: the tests to run before every commit, in about two minutes.

    .venv/Scripts/python.exe -m core.fast_tests            the set
    .venv/Scripts/python.exe -m core.fast_tests --keepdb   faster after the first run

The full suite (about 30 minutes) still runs before a push and in CI.
This set is what guards money and isolation, the things a commit must
never break: the tax engine and its browser copy, bills, payments,
discounts and offers, bill numbers, an order made once, one restaurant
never seeing another's rows, and the frontend's checks (the ratchet, the
modules, the node tests).

It runs as `manage.py test`, so core/settings.py sees a test run (no
rate limits, no Redis, the fast password hasher). The storage keys are
blanked first: the laptop's .env holds the production R2 keys, and a test
must never write to that bucket.

A module that is no longer worth its time here, or a new guard that is,
changes this list. A label is a test module, or a module and one class in
it. core/tests/test_fast_tests.py checks every label still names tests,
and that no browser or thread-race test slips in.
"""
import os
import sys

# Money: totals, tax, the bill and how it is paid.
MONEY = [
    "orders.tests.test_tax_engine",
    "orders.tests.test_tax_service",
    "orders.tests.test_tax_record",
    "orders.tests.test_cart_tax_js",
    "orders.tests.test_liquor_vat",
    "orders.tests.test_golden_totals",
    "orders.tests.test_totals_properties",
    "orders.tests.test_financial_flows",
    "orders.tests.test_pay_order_guards",
    # An order made once; its six-threads race runs in the full suite.
    "orders.tests.test_order_request_id.OrderRequestIdTest",
    "orders.tests.test_order_request_id.TokenCounterRequestIdTest",
    "orders.tests.test_issued_bill_guard.IssuedBillScreensTest",
    "orders.tests.test_printed_bill_approval",
    "orders.tests.test_discount_hardening",
    # Bill numbers without its race and its migration test (a minute between them).
    "orders.tests.test_bill_numbers.RulesTest",
    "orders.tests.test_bill_numbers.NumberingTest",
    "orders.tests.test_bill_numbers.PrintedNumberTest",
    "orders.tests.test_bill_numbers.BillCodeSettingTest",
    "orders.tests.test_bill_numbers.DemoResetTest",
    "payments.tests",
    "offers.tests.test_engine",
    "offers.tests.test_offers_js",
    "offers.tests.test_offers_on_bills",
    # Promos, without LastUseRaceTest (a thread race).
    "promos.tests.test_promo_rules.AuditCountsBillsTest",
    "promos.tests.test_promo_rules.BillNamesThePromoTest",
    "promos.tests.test_promo_rules.EditPromoTest",
    "promos.tests.test_promo_rules.EngineMinimumTest",
    "promos.tests.test_promo_rules.FlatPromoMinimumTest",
    "promos.tests.test_promo_rules.MinimumOrderTest",
    "promos.tests.test_promo_rules.MinimumWithTaxInsideTest",
    "promos.tests.test_promo_rules.OneOwnerOfTheDiscountTest",
    "promos.tests.test_promo_rules.SetupPermissionsTest",
    "promos.tests.test_promo_rules.SetupStatusTest",
    "promos.tests.test_promo_rules.UsesFromBillsTest",
]

# One restaurant never sees another's rows.
ISOLATION = [
    "core.tests.test_tenant_walk",
    "core.tests.test_tenant_isolation",
    "tenants.tests.test_isolation_check",
    "core.tests.test_url_names",
]

# The frontend's guards, and the node module tests (run by test_frontend_js).
FRONTEND = [
    "core.tests.test_frontend_hygiene",
    "core.tests.test_frontend_js",
    "core.tests.test_template_tags_closed",
    "core.tests.test_static_modules",
    "core.tests.test_test_speed",
    "core.tests.test_fast_tests",
]

FAST = MONEY + ISOLATION + FRONTEND

STORAGE_KEYS = ("AWS_STORAGE_BUCKET_NAME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_S3_ENDPOINT_URL")


def argv(extra):
    """manage.py test, the set, eight processes, plus whatever was typed."""
    parallel = [] if any(a.startswith("--parallel") for a in extra) else ["--parallel", "8"]
    return ["manage.py", "test", "--noinput", *parallel, *extra, *FAST]


def main(extra):
    for key in STORAGE_KEYS:
        os.environ[key] = ""        # set, so .env cannot fill it in (load_dotenv never overrides)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    sys.argv = argv(extra)
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main(sys.argv[1:])
