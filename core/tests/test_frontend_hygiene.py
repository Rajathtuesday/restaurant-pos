"""
The ratchet: templates can only get cleaner.

For every template this counts
  script       lines inside inline <script> blocks (the one boot script in
               base.html, marked data-rasova-boot, and json_script blocks
               do not count: they are the two inline forms Rasova allows)
  style        lines inside inline <style> blocks
  onclick      onclick= attributes
  third_party  references to a CDN or Google Fonts host
  globals      lines at the top level of a page script that read one of
               the shared objects (apiClient, ui, offlineQueue, ...). Those
               come from core/app.js, a module, which runs after the page
               is parsed; a top-level read runs before it and finds nothing.
               Inside a function or a DOMContentLoaded handler is fine.

BASELINE holds the numbers on the day the ratchet was set. A file may not
get worse than its entry; and an entry may not be looser than the file now
is, so when a page is migrated its entry must be lowered (or deleted when
every count is zero). Each step of the frontend plan shrinks BASELINE, and
when it is empty this test keeps it empty: a new page that puts code
inline fails here, with the numbers, before it reaches anyone.

To refresh the numbers after migrating pages (it prints the dictionary):
    python -m core.tests.test_frontend_hygiene

Run: python manage.py test core.tests.test_frontend_hygiene
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from core.testing import not_in_compiled_build

BASE = Path(settings.BASE_DIR)

SCRIPT_OPEN = re.compile(r"<script\b([^>]*)>", re.I)
SCRIPT_CLOSE = re.compile(r"</script\s*>", re.I)
STYLE_OPEN = re.compile(r"<style\b[^>]*>", re.I)
STYLE_CLOSE = re.compile(r"</style\s*>", re.I)
THIRD_PARTY = re.compile(r"https?://(cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com|fonts\.googleapis\.com|fonts\.gstatic\.com|code\.jquery\.com)")
SHARED_GLOBALS = re.compile(r"\b(apiClient|ui\.(toast|confirm|showLoading|hideLoading)|themeManager|offlineQueue|offlinePaymentQueue|rasovaPlatform|refreshNotifications|updateOfflineBanner|RasovaCartTax|RasovaOffers|OrderEdit|RasovaApproval)\b")
TOP_LEVEL_SKIP = re.compile(r"^\s*(//|/\*|\*)")   # a comment mentioning a global is not a read

SKIP_PARTS = {"md_files", "node_modules", "staticfiles", "rasova_android", "public", "js", "docs", ".venv", ".code-review-graph", "graft"}


def app_templates():
    for path in sorted(BASE.rglob("*.html")):
        rel = path.relative_to(BASE)
        if rel.parts[0] in SKIP_PARTS or any(p in SKIP_PARTS for p in rel.parts[:-1]):
            continue
        yield path


def measure(text):
    """The five counts for one template's text."""
    script = style = globals_ = 0
    onclick = len(re.findall(r"\bonclick\s*=", text, re.I))
    third_party = len(THIRD_PARTY.findall(text))

    in_script = in_style = False
    allowed_block = False
    depth = 0
    for line in text.splitlines():
        if in_script:
            if SCRIPT_CLOSE.search(line):
                in_script = False
                continue
            if allowed_block:
                continue
            script += 1
            # A line that defines a function (=> or function) runs later, not now.
            lazy = "=>" in line or "function" in line
            if depth == 0 and not lazy and SHARED_GLOBALS.search(line) and not TOP_LEVEL_SKIP.match(line):
                globals_ += 1
            depth += line.count("{") - line.count("}")
            if depth < 0:
                depth = 0
            continue
        if in_style:
            if STYLE_CLOSE.search(line):
                in_style = False
                continue
            style += 1
            continue
        opened = SCRIPT_OPEN.search(line)
        if opened and not SCRIPT_CLOSE.search(line[opened.end():]):
            attrs = opened.group(1)
            # src= scripts have no body; json_script and the boot script are
            # the two inline forms that are allowed.
            allowed_block = "src=" in attrs or "application/json" in attrs or "data-rasova-boot" in attrs
            in_script = True
            depth = 0
            continue
        if opened and "src=" not in opened.group(1) and "application/json" not in opened.group(1) and "data-rasova-boot" not in opened.group(1):
            script += 1      # a one-line inline script
        if STYLE_OPEN.search(line) and not STYLE_CLOSE.search(line):
            in_style = True
    return {"script": script, "style": style, "onclick": onclick, "third_party": third_party, "globals": globals_}


def measure_all():
    found = {}
    for path in app_templates():
        counts = measure(path.read_text(encoding="utf-8", errors="ignore"))
        if any(counts.values()):
            found[path.relative_to(BASE).as_posix()] = counts
    return found


# The numbers on 11 November 2026, when the ratchet was set. Only ever lowered.
BASELINE = {
    'accounts/templates/accounts/feature_flags.html': {"script": 29, "style": 65, "onclick": 1, "third_party": 0, "globals": 0},
    'accounts/templates/accounts/login.html': {"script": 13, "style": 298, "onclick": 1, "third_party": 0, "globals": 0},
    'accounts/templates/accounts/owner_dashboard.html': {"script": 71, "style": 193, "onclick": 4, "third_party": 0, "globals": 0},
    'accounts/templates/accounts/sales_dashboard.html': {"script": 0, "style": 23, "onclick": 0, "third_party": 0, "globals": 0},
    'accounts/templates/accounts/superuser_panel.html': {"script": 39, "style": 31, "onclick": 1, "third_party": 0, "globals": 0},
    'accounts/templates/accounts/superuser_tenant.html': {"script": 11, "style": 18, "onclick": 1, "third_party": 0, "globals": 0},
    'agency/templates/agency/dashboard.html': {"script": 0, "style": 11, "onclick": 1, "third_party": 0, "globals": 0},
    'billing/templates/billing/invoice.html': {"script": 0, "style": 18, "onclick": 0, "third_party": 0, "globals": 0},
    'box/templates/box/health.html': {"script": 0, "style": 15, "onclick": 0, "third_party": 0, "globals": 0},
    'box/templates/box/setup.html': {"script": 0, "style": 13, "onclick": 0, "third_party": 0, "globals": 0},
    'core/templates/core/demo_switch.html': {"script": 0, "style": 105, "onclick": 0, "third_party": 0, "globals": 0},
    'covers/templates/covers/covers.html': {"script": 0, "style": 30, "onclick": 1, "third_party": 0, "globals": 0},
    'crm/templates/crm/crm_dashboard.html': {"script": 0, "style": 13, "onclick": 0, "third_party": 0, "globals": 0},
    'crm/templates/crm/guest_profile.html': {"script": 0, "style": 13, "onclick": 0, "third_party": 0, "globals": 0},
    'crm/templates/crm/reservations.html': {"script": 33, "style": 16, "onclick": 6, "third_party": 0, "globals": 0},
    'finance/templates/finance/expense_list.html': {"script": 29, "style": 30, "onclick": 3, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/bar_count.html': {"script": 0, "style": 18, "onclick": 0, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/bar_count_list.html': {"script": 0, "style": 11, "onclick": 0, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/bar_count_result.html': {"script": 0, "style": 18, "onclick": 0, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/batch_detail.html': {"script": 66, "style": 40, "onclick": 3, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/central_kitchen.html': {"script": 87, "style": 67, "onclick": 7, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/consumption_report.html': {"script": 16, "style": 45, "onclick": 1, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/inventory_board.html': {"script": 186, "style": 74, "onclick": 11, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/kegs.html': {"script": 0, "style": 23, "onclick": 0, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/purchase_order_print.html': {"script": 0, "style": 318, "onclick": 2, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/purchase_orders.html': {"script": 246, "style": 77, "onclick": 14, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/receive_transfers.html': {"script": 55, "style": 38, "onclick": 2, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/recipe_import_review.html': {"script": 108, "style": 29, "onclick": 3, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/requisition_detail.html': {"script": 48, "style": 34, "onclick": 4, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/requisitions.html': {"script": 89, "style": 51, "onclick": 6, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/suppliers.html': {"script": 55, "style": 45, "onclick": 2, "third_party": 0, "globals": 0},
    'inventory/templates/inventory/variance_report.html': {"script": 16, "style": 51, "onclick": 1, "third_party": 0, "globals": 0},
    'kitchen/templates/kitchen/kitchen.html': {"script": 151, "style": 187, "onclick": 8, "third_party": 0, "globals": 0},
    'menu/templates/menu/digital_menu.html': {"script": 803, "style": 518, "onclick": 21, "third_party": 0, "globals": 0},
    'menu/templates/menu/gst_management.html': {"script": 64, "style": 115, "onclick": 3, "third_party": 0, "globals": 0},
    'menu/templates/menu/menu_management.html': {"script": 279, "style": 104, "onclick": 28, "third_party": 0, "globals": 0},
    'menu/templates/menu/modifiers_management.html': {"script": 139, "style": 46, "onclick": 10, "third_party": 0, "globals": 0},
    'menu/templates/menu/qsr_menu_management.html': {"script": 208, "style": 96, "onclick": 15, "third_party": 0, "globals": 0},
    'orders/templates/orders/live_orders.html': {"script": 195, "style": 93, "onclick": 1, "third_party": 0, "globals": 0},
    'orders/templates/orders/order_history.html': {"script": 223, "style": 172, "onclick": 8, "third_party": 0, "globals": 0},
    'orders/templates/orders/order_locked.html': {"script": 11, "style": 114, "onclick": 1, "third_party": 0, "globals": 0},
    'orders/templates/orders/order_timeline.html': {"script": 0, "style": 52, "onclick": 0, "third_party": 0, "globals": 0},
    'orders/templates/orders/public_bill.html': {"script": 0, "style": 54, "onclick": 0, "third_party": 0, "globals": 0},
    'orders/templates/orders/public_bill_expired.html': {"script": 0, "style": 16, "onclick": 0, "third_party": 0, "globals": 0},
    'orders/templates/orders/public_feedback.html': {"script": 0, "style": 53, "onclick": 0, "third_party": 0, "globals": 0},
    'orders/templates/orders/running_order.html': {"script": 146, "style": 116, "onclick": 6, "third_party": 0, "globals": 0},
    'orders/templates/orders/tables.html': {"script": 345, "style": 148, "onclick": 28, "third_party": 0, "globals": 0},
    'orders/templates/orders/thermal_receipt.html': {"script": 10, "style": 67, "onclick": 0, "third_party": 0, "globals": 0},
    'payments/templates/payments/pending_refunds.html': {"script": 35, "style": 15, "onclick": 2, "third_party": 0, "globals": 0},
    'reports/templates/reports/_report_tabs.html': {"script": 7, "style": 0, "onclick": 0, "third_party": 0, "globals": 0},
    'reports/templates/reports/audit_report.html': {"script": 0, "style": 47, "onclick": 1, "third_party": 0, "globals": 0},
    'reports/templates/reports/crm_analytics.html': {"script": 0, "style": 45, "onclick": 1, "third_party": 0, "globals": 0},
    'reports/templates/reports/dashboard.html': {"script": 116, "style": 120, "onclick": 12, "third_party": 0, "globals": 0},
    'reports/templates/reports/inspect.html': {"script": 0, "style": 140, "onclick": 1, "third_party": 0, "globals": 0},
    'reports/templates/reports/inventory_report.html': {"script": 22, "style": 127, "onclick": 11, "third_party": 0, "globals": 0},
    'reports/templates/reports/kitchen_dashboard.html': {"script": 0, "style": 49, "onclick": 1, "third_party": 0, "globals": 0},
    'reports/templates/reports/labor_report.html': {"script": 0, "style": 48, "onclick": 1, "third_party": 0, "globals": 0},
    'reports/templates/reports/menu_engineering.html': {"script": 0, "style": 52, "onclick": 1, "third_party": 0, "globals": 0},
    'setup/templates/setup/aggregator_config.html': {"script": 0, "style": 37, "onclick": 1, "third_party": 0, "globals": 0},
    'setup/templates/setup/approval_pin.html': {"script": 0, "style": 19, "onclick": 1, "third_party": 0, "globals": 0},
    'setup/templates/setup/onboard.html': {"script": 108, "style": 145, "onclick": 4, "third_party": 0, "globals": 0},
    'setup/templates/setup/outlet_settings.html': {"script": 65, "style": 40, "onclick": 1, "third_party": 0, "globals": 0},
    'setup/templates/setup/printer_setup.html': {"script": 53, "style": 108, "onclick": 4, "third_party": 0, "globals": 0},
    'setup/templates/setup/report_subscriptions.html': {"script": 34, "style": 27, "onclick": 4, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup.html': {"script": 0, "style": 38, "onclick": 1, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup_kitchen_stations.html': {"script": 235, "style": 92, "onclick": 10, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup_menu.html': {"script": 0, "style": 22, "onclick": 1, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup_offers.html': {"script": 250, "style": 91, "onclick": 7, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup_payment_methods.html': {"script": 0, "style": 33, "onclick": 2, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup_promos.html': {"script": 132, "style": 103, "onclick": 6, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup_qr_codes.html': {"script": 0, "style": 21, "onclick": 2, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup_staff.html': {"script": 146, "style": 42, "onclick": 6, "third_party": 0, "globals": 0},
    'setup/templates/setup/setup_tables.html': {"script": 9, "style": 25, "onclick": 2, "third_party": 0, "globals": 0},
    'shifts/templates/shifts/cash_sessions.html': {"script": 10, "style": 11, "onclick": 3, "third_party": 0, "globals": 0},
    'shifts/templates/shifts/schedule_builder.html': {"script": 59, "style": 37, "onclick": 5, "third_party": 0, "globals": 0},
    'shifts/templates/shifts/shift_dashboard.html': {"script": 30, "style": 11, "onclick": 4, "third_party": 0, "globals": 0},
    'shifts/templates/shifts/shift_templates.html': {"script": 32, "style": 19, "onclick": 3, "third_party": 0, "globals": 0},
    'templates/core/demo_switch.html': {"script": 0, "style": 105, "onclick": 0, "third_party": 0, "globals": 0},
    'tokens/templates/tokens/display_board.html': {"script": 50, "style": 56, "onclick": 0, "third_party": 0, "globals": 0},
    'tokens/templates/tokens/token_dashboard.html': {"script": 84, "style": 86, "onclick": 7, "third_party": 0, "globals": 0},
    'waiter/templates/waiter/waiter_dashboard.html': {"script": 30, "style": 73, "onclick": 3, "third_party": 0, "globals": 0},
}


class FrontendHygieneTest(SimpleTestCase):
    @not_in_compiled_build("the templates' source")
    def test_templates_only_get_cleaner(self):
        now = measure_all()
        problems = []
        for rel, counts in sorted(now.items()):
            allowed = BASELINE.get(rel)
            if allowed is None:
                problems.append(f"{rel}: has inline code and is not in BASELINE: {counts}")
                continue
            for key, value in counts.items():
                if value > allowed.get(key, 0):
                    problems.append(f"{rel}: {key} went up to {value} (allowed {allowed.get(key, 0)}); move it to static/")
        for rel, allowed in sorted(BASELINE.items()):
            counts = now.get(rel, {})
            for key, value in allowed.items():
                if value > counts.get(key, 0):
                    problems.append(
                        f"{rel}: BASELINE allows {key}={value} but the file has {counts.get(key, 0)}; "
                        "lower the entry (python -m core.tests.test_frontend_hygiene --write)"
                    )
            if not (BASE / rel).exists():
                problems.append(f"{rel}: in BASELINE but the file is gone; delete the entry")
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_measure_counts_what_it_says(self):
        text = (
            '<script src="x.js"></script>\n'
            '<script type="application/json" id="d">{"a": 1}</script>\n'
            "<script data-rasova-boot>boot();</script>\n"
            "<script>\nconst CSRF = apiClient.getCsrf();\nfunction f() {\n  ui.toast('x');\n}\n</script>\n"
            "<style>\na {}\nb {}\n</style>\n"
            '<button onclick="f()">x</button>\n'
            '<link href="https://fonts.googleapis.com/css2?family=Outfit">\n'
        )
        self.assertEqual(
            measure(text),
            {"script": 4, "style": 2, "onclick": 1, "third_party": 1, "globals": 1},
        )


if __name__ == "__main__":
    import json
    import sys

    import django

    django.setup()
    data = measure_all()
    text = "BASELINE = {\n" + "".join(
        f"    {rel!r}: {json.dumps(counts)},\n" for rel, counts in sorted(data.items())
    ) + "}\n"
    if "--write" in sys.argv:
        path = Path(__file__)
        source = path.read_text(encoding="utf-8")
        start = source.index("BASELINE = {")
        end = source.index("}\n", start) + 2
        path.write_text(source[:start] + text + source[end:], encoding="utf-8")
        print(f"wrote {len(data)} entries to {path.name}")
    else:
        print(text)
