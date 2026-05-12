# reports/tests/test_reports.py
"""
Test suite for the reports application.
Covers:
  - reports.services.sales_reports: daily_sales, hourly_sales
  - reports.services.item_reports: top_items
  - reports.services.kitchen_reports: kitchen_performance, top_kitchen_items
  - reports.services.export_services: CSV and Excel generation
  - reports.views: dashboard and export_reports HTTP endpoints
"""

import io
import csv
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from tenants.models import Tenant, Outlet
from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Payment


# ---------------------------------------------------------------------------
# SHARED FIXTURE HELPER
# ---------------------------------------------------------------------------

def create_base_fixtures():
    """Creates reusable Tenant, Outlet, User, and MenuItem objects."""
    tenant = Tenant.objects.create(name="Test Restaurant")
    outlet = Outlet.objects.create(tenant=tenant, name="Main Branch")
    user = User.objects.create_user(
        username="owner_test",
        password="pass1234",
        tenant=tenant,
        outlet=outlet,
        role="owner"
    )
    category = MenuCategory.objects.create(
        tenant=tenant,
        outlet=outlet,
        name="Starters"
    )
    item = MenuItem.objects.create(
        tenant=tenant,
        outlet=outlet,
        category=category,
        name="Paneer Tikka",
        price=Decimal("200.00"),
        gst_percentage=Decimal("5.00"),
        is_available=True
    )
    return tenant, outlet, user, item


def create_paid_order(tenant, outlet, user, item, grand_total=500, method="cash"):
    """Creates a paid order with an associated payment."""
    order = Order.objects.create(
        tenant=tenant,
        outlet=outlet,
        created_by=user,
        status="paid",
        subtotal=Decimal(str(grand_total)),
        gst_total=Decimal("0.00"),
        discount_total=Decimal("0.00"),
        grand_total=Decimal(str(grand_total))
    )
    Payment.objects.create(
        order=order,
        method=method,
        amount=Decimal(str(grand_total)),
        created_by=user
    )
    if item:
        OrderItem.objects.create(
            order=order,
            menu_item=item,
            quantity=2,
            price=item.price,
            gst_percentage=item.gst_percentage,
            total_price=Decimal("400.00"),
            status="served"
        )
    return order


# ---------------------------------------------------------------------------
# 1. SALES REPORT SERVICE TESTS
# ---------------------------------------------------------------------------

class DailySalesServiceTest(TestCase):
    """Tests for reports.services.sales_reports.daily_sales"""

    def setUp(self):
        self.tenant, self.outlet, self.user, self.item = create_base_fixtures()

    def test_daily_sales_returns_correct_total(self):
        """Revenue should sum all payments for today."""
        create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=500)
        create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=300)

        from reports.services.sales_reports import daily_sales
        result = daily_sales(self.tenant, self.outlet)

        self.assertEqual(result["total_sales"], 800.0)

    def test_daily_sales_zero_when_no_orders(self):
        """Revenue should be 0 when no orders exist."""
        from reports.services.sales_reports import daily_sales
        result = daily_sales(self.tenant, self.outlet)

        self.assertEqual(result["total_sales"], 0.0)
        self.assertEqual(result["orders"], 0)

    def test_daily_sales_counts_orders_correctly(self):
        """Order count should match the number of paid orders."""
        create_paid_order(self.tenant, self.outlet, self.user, self.item)
        create_paid_order(self.tenant, self.outlet, self.user, self.item)

        from reports.services.sales_reports import daily_sales
        result = daily_sales(self.tenant, self.outlet)

        self.assertEqual(result["orders"], 2)

    def test_daily_sales_isolates_by_outlet(self):
        """Sales for outlet A must not bleed into outlet B."""
        other_outlet = Outlet.objects.create(tenant=self.tenant, name="Branch B")
        other_user = User.objects.create_user(
            username="owner_b", password="pass", tenant=self.tenant,
            outlet=other_outlet, role="owner"
        )
        create_paid_order(self.tenant, other_outlet, other_user, self.item, grand_total=999)
        create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=200)

        from reports.services.sales_reports import daily_sales
        result = daily_sales(self.tenant, self.outlet)

        self.assertEqual(result["total_sales"], 200.0)

    def test_daily_sales_average_order_value(self):
        """AOV should be total_sales / order_count."""
        create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=300)
        create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=700)

        from reports.services.sales_reports import daily_sales
        result = daily_sales(self.tenant, self.outlet)

        self.assertEqual(result["avg_order_value"], 500.0)

    def test_daily_sales_excludes_refunds_from_payment_split(self):
        """Refund payments should appear as 'net_refunds', not in payment methods."""
        order = create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=500)
        Payment.objects.create(
            order=order, method="refund",
            amount=Decimal("-200.00"), created_by=self.user
        )

        from reports.services.sales_reports import daily_sales
        result = daily_sales(self.tenant, self.outlet)

        methods = [p["method"] for p in result["payments"]]
        self.assertNotIn("refund", methods)
        self.assertEqual(result["net_refunds"], 200.0)

    def test_daily_sales_correct_payment_split(self):
        """Payment split should show the correct totals per method."""
        create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=400, method="cash")
        create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=600, method="upi")

        from reports.services.sales_reports import daily_sales
        result = daily_sales(self.tenant, self.outlet)

        split = {p["method"]: p["total"] for p in result["payments"]}
        self.assertEqual(float(split["cash"]), 400.0)
        self.assertEqual(float(split["upi"]), 600.0)


# ---------------------------------------------------------------------------
# 2. ITEM REPORT SERVICE TESTS
# ---------------------------------------------------------------------------

class TopItemsServiceTest(TestCase):
    """Tests for reports.services.item_reports.top_items"""

    def setUp(self):
        self.tenant, self.outlet, self.user, self.item = create_base_fixtures()

    def test_top_items_returns_items(self):
        """top_items should return sold items."""
        create_paid_order(self.tenant, self.outlet, self.user, self.item)

        from reports.services.item_reports import top_items
        result = list(top_items(self.tenant, self.outlet))

        self.assertTrue(len(result) > 0)
        self.assertEqual(result[0]["menu_item__name"], "Paneer Tikka")

    def test_top_items_empty_when_no_orders(self):
        """top_items should be empty when no orders exist."""
        from reports.services.item_reports import top_items
        result = list(top_items(self.tenant, self.outlet))

        self.assertEqual(len(result), 0)

    def test_top_items_orders_by_quantity_descending(self):
        """Most sold item should appear first."""
        category = MenuCategory.objects.get(tenant=self.tenant)
        item2 = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=category,
            name="Chicken Wings", price=Decimal("300.00"),
            gst_percentage=Decimal("5.00"), is_available=True
        )
        order1 = create_paid_order(self.tenant, self.outlet, self.user, self.item)
        order2 = create_paid_order(self.tenant, self.outlet, self.user, None, grand_total=600)
        # Add 5 wings to order2 to make it the top item
        for _ in range(3):
            OrderItem.objects.create(
                order=order2, menu_item=item2, quantity=5,
                price=item2.price, gst_percentage=item2.gst_percentage,
                total_price=Decimal("1500.00"), status="served"
            )

        from reports.services.item_reports import top_items
        result = list(top_items(self.tenant, self.outlet))

        self.assertEqual(result[0]["menu_item__name"], "Chicken Wings")


# ---------------------------------------------------------------------------
# 3. KITCHEN REPORT SERVICE TESTS
# ---------------------------------------------------------------------------

class KitchenReportServiceTest(TestCase):
    """Tests for reports.services.kitchen_reports"""

    def setUp(self):
        self.tenant, self.outlet, self.user, self.item = create_base_fixtures()

    def test_kitchen_performance_returns_zeroes_when_empty(self):
        """kitchen_performance should return zeros when no kitchen data exists."""
        from reports.services.kitchen_reports import kitchen_performance
        result = kitchen_performance(self.tenant, self.outlet)

        self.assertEqual(result["total_items_prepared"], 0)
        self.assertEqual(result["total_kots"], 0)

    def test_top_kitchen_items_empty_when_no_kots(self):
        """top_kitchen_items should return empty list when no KOT has been sent."""
        from reports.services.kitchen_reports import top_kitchen_items
        result = top_kitchen_items(self.tenant, self.outlet)

        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# 4. EXPORT SERVICE TESTS
# ---------------------------------------------------------------------------

class ExportServicesTest(TestCase):
    """Tests for reports.services.export_services"""

    def setUp(self):
        self.tenant, self.outlet, self.user, self.item = create_base_fixtures()
        self.today = timezone.localdate()
        create_paid_order(self.tenant, self.outlet, self.user, self.item, grand_total=500)

    def test_generate_orders_csv_returns_string(self):
        """Should return a non-empty CSV string."""
        from reports.services.export_services import generate_orders_csv
        result = generate_orders_csv(self.tenant, self.outlet, self.today, self.today)

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_generate_orders_csv_has_correct_headers(self):
        """CSV should contain expected column headers."""
        from reports.services.export_services import generate_orders_csv
        result = generate_orders_csv(self.tenant, self.outlet, self.today, self.today)
        reader = csv.reader(io.StringIO(result))
        headers = next(reader)

        self.assertIn("Order ID", headers)
        self.assertIn("Order No", headers)
        self.assertIn("Grand Total", headers)

    def test_generate_orders_csv_contains_order_data(self):
        """CSV data rows should reflect the created order."""
        from reports.services.export_services import generate_orders_csv
        result = generate_orders_csv(self.tenant, self.outlet, self.today, self.today)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        rows = list(reader)

        self.assertEqual(len(rows), 1)
        # Grand total column (index 12) should be 500
        self.assertIn("500", rows[0][12])

    def test_generate_items_csv_returns_string(self):
        """Should return a non-empty CSV string."""
        from reports.services.export_services import generate_items_csv
        result = generate_items_csv(self.tenant, self.outlet, self.today, self.today)

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_generate_items_csv_has_correct_headers(self):
        """Items CSV should contain expected columns."""
        from reports.services.export_services import generate_items_csv
        result = generate_items_csv(self.tenant, self.outlet, self.today, self.today)
        reader = csv.reader(io.StringIO(result))
        headers = next(reader)

        self.assertIn("Item Name", headers)
        self.assertIn("Quantity Sold", headers)
        self.assertIn("Average Rate", headers)

    def test_generate_items_csv_contains_item_data(self):
        """Items CSV should contain the sold item name."""
        from reports.services.export_services import generate_items_csv
        result = generate_items_csv(self.tenant, self.outlet, self.today, self.today)

        self.assertIn("Paneer Tikka", result)

    def test_generate_waiter_csv_returns_string(self):
        """Waiter CSV should return a non-empty string."""
        from reports.services.export_services import generate_waiter_csv
        result = generate_waiter_csv(self.tenant, self.outlet, self.today, self.today)

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_generate_waiter_csv_has_correct_headers(self):
        """Waiter CSV should contain expected columns."""
        from reports.services.export_services import generate_waiter_csv
        result = generate_waiter_csv(self.tenant, self.outlet, self.today, self.today)
        reader = csv.reader(io.StringIO(result))
        headers = next(reader)

        self.assertIn("Staff Name", headers)
        self.assertIn("Total Revenue Handled", headers)
        self.assertIn("Average Order Value", headers)

    def test_generate_category_csv_returns_string(self):
        """Category CSV should return a non-empty string."""
        from reports.services.export_services import generate_category_csv
        result = generate_category_csv(self.tenant, self.outlet, self.today, self.today)

        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_generate_category_csv_has_correct_headers(self):
        """Category CSV should contain expected columns."""
        from reports.services.export_services import generate_category_csv
        result = generate_category_csv(self.tenant, self.outlet, self.today, self.today)
        reader = csv.reader(io.StringIO(result))
        headers = next(reader)

        self.assertIn("Category Name", headers)
        self.assertIn("Items Sold", headers)
        self.assertIn("Total Revenue", headers)

    def test_generate_gstr1_excel_returns_bytes(self):
        """GSTR-1 should return a bytes object (valid .xlsx binary)."""
        from reports.services.export_services import generate_gstr1_excel
        result = generate_gstr1_excel(self.tenant, self.outlet, self.today, self.today)

        self.assertIsInstance(result, bytes)
        self.assertGreater(len(result), 0)

    def test_generate_gstr1_excel_is_valid_xlsx(self):
        """GSTR-1 bytes should be loadable as a valid openpyxl workbook with correct structure."""
        import openpyxl
        from reports.services.export_services import generate_gstr1_excel
        result = generate_gstr1_excel(self.tenant, self.outlet, self.today, self.today)
        wb = openpyxl.load_workbook(io.BytesIO(result))

        self.assertIsNotNone(wb)
        ws = wb.active

        # Row 1 is the merged title cell
        self.assertIn(self.tenant.name, ws['A1'].value)

        # Row 4 is the header row (row1=title, row2=period, row3=empty, row4=headers)
        header_values = [ws.cell(row=4, column=i).value for i in range(1, 11)]
        self.assertIn("Rate (%)", header_values)
        self.assertIn("Central Tax (CGST)", header_values)
        self.assertIn("State Tax (SGST)", header_values)
        self.assertIn("Taxable Value", header_values)

    def test_generate_orders_csv_empty_when_no_orders_in_range(self):
        """CSV with a past date range should only have the header, no data rows."""
        from reports.services.export_services import generate_orders_csv
        from datetime import date
        result = generate_orders_csv(self.tenant, self.outlet, date(2000, 1, 1), date(2000, 1, 2))
        reader = csv.reader(io.StringIO(result))
        next(reader)  # header
        rows = list(reader)
        self.assertEqual(len(rows), 0)


# ---------------------------------------------------------------------------
# 5. REPORTS VIEW (HTTP ENDPOINT) TESTS
# ---------------------------------------------------------------------------

class ReportsDashboardViewTest(TestCase):
    """Tests for reports.views.dashboard (HTTP)"""

    def setUp(self):
        self.client = Client()
        self.tenant, self.outlet, self.user, self.item = create_base_fixtures()

    def test_dashboard_requires_login(self):
        """Unauthenticated users should be redirected to login."""
        response = self.client.get("/reports/dashboard/")
        self.assertIn(response.status_code, [302, 403])

    def test_dashboard_loads_for_owner(self):
        """Authenticated owner should get HTTP 200."""
        self.client.login(username="owner_test", password="pass1234")
        response = self.client.get("/reports/dashboard/")
        self.assertEqual(response.status_code, 200)

    def test_dashboard_forbidden_for_staff_without_role(self):
        """A user with no special role should be forbidden."""
        staff = User.objects.create_user(
            username="plain_staff", password="pass",
            tenant=self.tenant, outlet=self.outlet, role="staff"
        )
        self.client.login(username="plain_staff", password="pass")
        response = self.client.get("/reports/dashboard/")
        self.assertEqual(response.status_code, 403)


class ExportReportsViewTest(TestCase):
    """Tests for reports.views.export_reports (HTTP)"""

    def setUp(self):
        self.client = Client()
        self.tenant, self.outlet, self.user, self.item = create_base_fixtures()
        self.today = timezone.localdate().isoformat()
        create_paid_order(self.tenant, self.outlet, self.user, self.item)
        self.client.login(username="owner_test", password="pass1234")

    def _get_export(self, export_type):
        return self.client.get(
            f"/reports/export/?type={export_type}&date_filter=today"
        )

    def test_export_orders_csv_returns_200(self):
        """Orders CSV export endpoint should respond with HTTP 200."""
        response = self._get_export("orders")
        self.assertEqual(response.status_code, 200)

    def test_export_orders_csv_content_type(self):
        """Orders export should return text/csv content-type."""
        response = self._get_export("orders")
        self.assertIn("text/csv", response["Content-Type"])

    def test_export_orders_csv_has_attachment_header(self):
        """Orders export should have Content-Disposition attachment header."""
        response = self._get_export("orders")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".csv", response["Content-Disposition"])

    def test_export_items_csv_returns_200(self):
        """Items CSV export should return HTTP 200."""
        response = self._get_export("items")
        self.assertEqual(response.status_code, 200)

    def test_export_waiters_csv_returns_200(self):
        """Waiters CSV export should return HTTP 200."""
        response = self._get_export("waiters")
        self.assertEqual(response.status_code, 200)

    def test_export_categories_csv_returns_200(self):
        """Categories CSV export should return HTTP 200."""
        response = self._get_export("categories")
        self.assertEqual(response.status_code, 200)

    def test_export_gstr1_excel_returns_200(self):
        """GSTR-1 Excel export should return HTTP 200."""
        response = self._get_export("gstr1")
        self.assertEqual(response.status_code, 200)

    def test_export_gstr1_content_type_is_excel(self):
        """GSTR-1 export should return xlsx content-type."""
        response = self._get_export("gstr1")
        self.assertIn(
            "spreadsheetml",
            response["Content-Type"]
        )

    def test_export_invalid_type_returns_403(self):
        """Requesting an unknown export type should return HTTP 403."""
        response = self._get_export("unknown_type_xyz")
        self.assertEqual(response.status_code, 403)

    def test_export_requires_login(self):
        """Unauthenticated user should not access export endpoint."""
        self.client.logout()
        response = self._get_export("orders")
        self.assertIn(response.status_code, [302, 403])