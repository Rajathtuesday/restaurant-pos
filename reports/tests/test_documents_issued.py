"""
GSTR-1 Table 13, documents issued (P23): one row per bill number series in
the period, with the first and last number, how many bills, and how many
were cancelled. reports/services/documents_issued.py.

Run: python manage.py test reports.tests.test_documents_issued
"""
import io
from decimal import Decimal as D

import openpyxl
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem
from orders.services.bill_numbers import financial_year, split_bill_number
from reports.services.documents_issued import series_rows
from reports.services.export_services import generate_gstr1_excel
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant


class RowsTest(SimpleTestCase):

    def test_a_bill_number_splits_into_its_series_and_place(self):
        self.assertEqual(split_bill_number("SG/2627/000123"), ("SG/2627", 123))
        self.assertEqual(split_bill_number("INV-6-20260928-0001"), ("INV-6-20260928", 1))
        self.assertIsNone(split_bill_number("GOLD-12x"))
        self.assertIsNone(split_bill_number(None))

    def test_one_row_per_series_with_the_cancelled_bills(self):
        rows = series_rows([
            ("SG/2627/000002", "paid"), ("SG/2627/000001", "closed"), ("SG/2627/000003", "cancelled"),
            ("SG2/2627/000001", "billing"),
        ])
        self.assertEqual(rows, [
            {"series": "SG/2627", "first": "SG/2627/000001", "last": "SG/2627/000003",
             "total": 3, "cancelled": 1, "net": 2},
            {"series": "SG2/2627", "first": "SG2/2627/000001", "last": "SG2/2627/000001",
             "total": 1, "cancelled": 0, "net": 1},
        ])

    def test_total_counts_the_bills_not_the_numbers_between_first_and_last(self):
        # 000004 went to a bill of the next period; this period's last bill is 000005
        rows = series_rows([("SG/2627/000003", "paid"), ("SG/2627/000005", "paid")])
        self.assertEqual((rows[0]["first"], rows[0]["last"], rows[0]["total"]),
                         ("SG/2627/000003", "SG/2627/000005", 2))

    def test_old_numbers_make_one_series_a_day(self):
        rows = series_rows([("INV-6-20260927-0002", "paid"), ("INV-6-20260928-0001", "paid"),
                            ("INV-6-20260928-0004", "closed")])
        self.assertEqual([(r["series"], r["total"]) for r in rows],
                         [("INV-6-20260927", 1), ("INV-6-20260928", 2)])


class Table13SheetTest(TestCase):
    """Spice Garden, two outlets, today's bills, through the real workbook."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Spice Garden")
        self.main = Outlet.objects.create(tenant=self.tenant, name="Indiranagar", gst_no=SAMPLE_GSTIN)
        self.second = Outlet.objects.create(tenant=self.tenant, name="Koramangala", gst_no=SAMPLE_GSTIN)
        self.owner = User.objects.create_user(username="docs_owner", password="pw", role="owner",
                                              tenant=self.tenant, outlet=self.main)
        self.today = get_business_date(timezone.now(), self.main)
        self.year = financial_year(self.today)

    def bill(self, outlet, *statuses):
        """An order at `outlet`, taken through each status in turn."""
        category = MenuCategory.objects.get_or_create(tenant=self.tenant, outlet=outlet, name="Food")[0]
        dosa = MenuItem.objects.get_or_create(tenant=self.tenant, outlet=outlet, category=category, name="Dosa",
                                              defaults={"price": D("100"), "gst_percentage": D("5")})[0]
        order = Order.objects.create(tenant=self.tenant, outlet=outlet, created_by=self.owner, source="counter")
        OrderItem.objects.create(order=order, menu_item=dosa, quantity=1, price=D("100"),
                                 gst_percentage=D("5"), total_price=D("100"))
        for status in statuses:
            order.status = status
            order.save(update_fields=["status"])
        return order

    def sheet(self, outlet=None):
        book = openpyxl.load_workbook(io.BytesIO(generate_gstr1_excel(self.tenant, outlet, self.today, self.today)))
        rows = [row for row in book["GSTR-1 Table 13 (Docs)"].iter_rows(min_row=5, values_only=True)
                if row[0] is not None]
        return rows

    def test_each_outlets_series_with_its_cancelled_bills(self):
        self.bill(self.main, "billing", "closed")        # SG/.../000001, paid
        self.bill(self.main, "billing", "cancelled")     # SG/.../000002, cancelled after its bill
        self.bill(self.main, "cancelled")                # never billed: no number, not a document
        self.bill(self.main, "billing")                  # SG/.../000003, shown, not paid yet
        self.bill(self.second, "closed")                 # SG2/.../000001
        rows = self.sheet()
        self.assertEqual(rows[:2], [
            ("Invoices for outward supply", f"SG/{self.year}/000001", f"SG/{self.year}/000003", 3, 1, 2),
            ("Invoices for outward supply", f"SG2/{self.year}/000001", f"SG2/{self.year}/000001", 1, 0, 1),
        ])
        self.assertEqual(rows[2], ("TOTAL", None, None, 4, 1, 3))

    def test_one_outlets_return_lists_only_its_series(self):
        self.bill(self.main, "closed")
        self.bill(self.second, "closed")
        rows = self.sheet(self.second)
        self.assertEqual([row[1] for row in rows if row[0] != "TOTAL" and row[1]], [f"SG2/{self.year}/000001"])
