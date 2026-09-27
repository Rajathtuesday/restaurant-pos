"""
Golden reports: every report and export that shows money, run over a fixed
month of trading (golden_month.py), must give exactly the numbers in
reports/tests/golden/reports_v1.json.

The liquor VAT work adds VAT to reports on purpose. Those changes must arrive
with a regenerated file whose diff was read; anything else that moves is a bug.

Regenerate only for a deliberate change, then review the diff:
    GOLDEN_UPDATE=1 python manage.py test reports.tests.test_golden_reports
"""
import csv
import datetime as dt
import io
import json
import os
import pathlib
from decimal import Decimal

import openpyxl
from django.db.models import QuerySet
from django.test import TestCase

from reports.services.category_reports import category_sales
from reports.services.comparison_reports import period_comparison
from reports.services.export_services import (
    generate_category_csv, generate_gstr1_excel, generate_items_csv, generate_orders_csv,
    generate_pl_csv,
)
from reports.services.item_reports import top_items
from reports.services.pl_reports import gross_margin_report, net_profit_report
from reports.services.sales_reports import daily_sales, hourly_sales
from reports.tests.golden_month import FIRST_DAY, LAST_DAY, build_month

GOLDEN = pathlib.Path(__file__).parent / "golden" / "reports_v1.json"

REPORTS = {
    "daily_sales": daily_sales,
    "hourly_sales": hourly_sales,
    "category_sales": category_sales,
    "top_items": top_items,
    "gross_margin": gross_margin_report,
    "net_profit": net_profit_report,
    "period_comparison": period_comparison,
}
CSV_EXPORTS = {
    "orders_csv": generate_orders_csv,
    "items_csv": generate_items_csv,
    "category_csv": generate_category_csv,
    "pl_csv": generate_pl_csv,
}


def plain(value):
    """Report output as plain JSON values that compare exactly."""
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, QuerySet)):
        return [plain(item) for item in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def csv_rows(text, hide_column="Order ID"):
    """CSV rows, with the database id column blanked (ids differ between runs)."""
    rows = list(csv.reader(io.StringIO(text)))
    for header_index, row in enumerate(rows):
        if hide_column in row:
            column = row.index(hide_column)
            for later in rows[header_index + 1:]:
                if len(later) > column:
                    later[column] = "(id)"
            break
    return rows


def workbook_cells(data):
    book = openpyxl.load_workbook(io.BytesIO(data))
    return {
        sheet.title: [plain(list(row)) for row in sheet.iter_rows(values_only=True)
                      if any(cell is not None for cell in row)]
        for sheet in book.worksheets
    }


def _in_a_fixed_order(name, output):
    """A few reports list rows in no particular order when values tie."""
    if name == "top_items":
        return sorted(output, key=lambda row: (-float(row["total"]), row["menu_item__name"]))
    if name == "category_sales":
        return sorted(output, key=lambda row: (-float(row["revenue"]), row["menu_item__category__name"]))
    if name == "daily_sales":
        output["payments"] = sorted(output["payments"], key=lambda row: row["method"])
    return output


def all_reports():
    tenant, outlets = build_month()
    out = {}
    for outlet in [*outlets, None]:
        scope = outlet.name if outlet else "(all outlets)"
        out[scope] = {
            name: _in_a_fixed_order(name, plain(report(tenant, outlet, FIRST_DAY, LAST_DAY)))
            for name, report in REPORTS.items()
        }
        out[scope]["gstr1_workbook"] = workbook_cells(generate_gstr1_excel(tenant, outlet, FIRST_DAY, LAST_DAY))
        if outlet:
            for name, export in CSV_EXPORTS.items():
                out[scope][name] = csv_rows(export(tenant, outlet, FIRST_DAY, LAST_DAY))
    return out


class GoldenReportsTest(TestCase):

    def test_every_report_matches_the_golden_file(self):
        actual = json.loads(json.dumps(all_reports()))
        if os.environ.get("GOLDEN_UPDATE") == "1":
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                              encoding="utf-8", newline="\n")
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        for scope in expected:
            for name in expected[scope]:
                with self.subTest(scope=scope, report=name):
                    self.assertEqual(actual.get(scope, {}).get(name), expected[scope][name])
        self.assertEqual(sorted(actual), sorted(expected), "a different set of outlets")
        for scope in actual:
            self.assertEqual(sorted(actual[scope]), sorted(expected.get(scope, {})),
                             f"a different set of reports for {scope}")
