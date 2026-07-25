# reports/tests/test_finance.py
"""
Hand-calculated regression tests for net_profit_report() and the shared
cogs.item_cogs_map() extraction it (and menu_engineering_report) depend on.

Run: python manage.py test reports.tests.test_finance
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from finance.models import Expense
from inventory.models import InventoryItem, Recipe
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Payment
from tenants.models import Outlet, Tenant


class NetProfitReportTest(TestCase):
    """
    A single paid order with a known recipe-costed item, plus known
    expenses, worked out by hand:

      Order: 1x item @ Rs500, composition-scheme outlet (GST = 0).
      Recipe: 2g of an ingredient costing Rs15.00/g -> COGS = 2 * 15.00 = 30.00.
      Gross profit = 500 - 0 (GST) - 30 (COGS) = 470.00
      Expenses in range: Rs200 + Rs100 = 300.00 (one Rs50 expense OUTSIDE the
      range must be excluded).
      Net profit = 470.00 - 300.00 = 170.00
      Net margin = 170 / 500 * 100 = 34.0%
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Finance Cafe")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant, name="Main", is_composition_scheme=True,
        )
        self.user = User.objects.create_user(
            username="fin_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.category = MenuCategory.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Mains",
        )
        self.menu_item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=self.category,
            name="Steak", price=Decimal("500.00"), gst_percentage=Decimal("0"),
        )
        self.inventory_item = InventoryItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Beef",
            unit="g", cost_price=Decimal("15.00"),
        )
        Recipe.objects.create(
            menu_item=self.menu_item, inventory_item=self.inventory_item,
            quantity_required=Decimal("2"), unit="g",
        )

        self.today = timezone.localdate()
        self.order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, created_by=self.user,
            status="paid", subtotal=Decimal("500.00"), gst_total=Decimal("0.00"),
            discount_total=Decimal("0.00"), grand_total=Decimal("500.00"),
        )
        Payment.objects.create(
            order=self.order, method="cash", amount=Decimal("500.00"), created_by=self.user,
        )
        OrderItem.objects.create(
            order=self.order, menu_item=self.menu_item, quantity=1,
            price=Decimal("500.00"), gst_percentage=Decimal("0"),
            total_price=Decimal("500.00"), status="pending",
        )

        Expense.objects.create(
            tenant=self.tenant, outlet=self.outlet, category="rent",
            amount=Decimal("200.00"), expense_date=self.today,
        )
        Expense.objects.create(
            tenant=self.tenant, outlet=self.outlet, category="marketing",
            amount=Decimal("100.00"), expense_date=self.today,
        )
        # Outside the report's date range -- must NOT be counted.
        Expense.objects.create(
            tenant=self.tenant, outlet=self.outlet, category="utilities",
            amount=Decimal("50.00"), expense_date=self.today - timezone.timedelta(days=10),
        )

    def test_cogs_computed_from_recipe(self):
        from reports.services.pl_reports import gross_margin_report
        result = gross_margin_report(self.tenant, self.outlet, self.today, self.today)
        self.assertEqual(result["cogs"], 30.00)
        self.assertEqual(result["gross_profit"], 470.00)

    def test_operating_expenses_exclude_out_of_range_row(self):
        from reports.services.pl_reports import net_profit_report
        result = net_profit_report(self.tenant, self.outlet, self.today, self.today)
        self.assertEqual(result["operating_expenses"], 300.00)

    def test_net_profit_exact(self):
        from reports.services.pl_reports import net_profit_report
        result = net_profit_report(self.tenant, self.outlet, self.today, self.today)
        self.assertEqual(result["net_profit"], 170.00)
        self.assertEqual(result["net_margin_pct"], 34.0)

    def test_expense_breakdown_by_category(self):
        from reports.services.pl_reports import net_profit_report
        result = net_profit_report(self.tenant, self.outlet, self.today, self.today)
        breakdown = {row["category"]: float(row["total"]) for row in result["expense_breakdown"]}
        self.assertEqual(breakdown, {"rent": 200.00, "marketing": 100.00})


class NetProfitOutletScopingTest(TestCase):
    """A tenant-wide expense (outlet=None) must count against every outlet's
    report, not just one -- it's real money spent regardless of which
    outlet's numbers are being viewed. An outlet-specific expense must NOT
    leak into a different outlet's report."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Multi Outlet Finance")
        self.outlet_a = Outlet.objects.create(tenant=self.tenant, name="A")
        self.outlet_b = Outlet.objects.create(tenant=self.tenant, name="B")
        self.today = timezone.localdate()

        Expense.objects.create(
            tenant=self.tenant, outlet=None, category="marketing",
            amount=Decimal("500.00"), expense_date=self.today,
        )
        Expense.objects.create(
            tenant=self.tenant, outlet=self.outlet_a, category="rent",
            amount=Decimal("1000.00"), expense_date=self.today,
        )

    def test_tenant_wide_expense_counts_for_both_outlets(self):
        from reports.services.pl_reports import net_profit_report
        result_a = net_profit_report(self.tenant, self.outlet_a, self.today, self.today)
        result_b = net_profit_report(self.tenant, self.outlet_b, self.today, self.today)
        # A: its own rent (1000) + the tenant-wide marketing (500) = 1500
        self.assertEqual(result_a["operating_expenses"], 1500.00)
        # B: ONLY the tenant-wide marketing (500) -- not A's rent
        self.assertEqual(result_b["operating_expenses"], 500.00)


class ExpenseCrossTenantIsolationTest(TestCase):
    def test_other_tenants_expense_not_counted(self):
        tenant_a = Tenant.objects.create(name="Tenant A Fin")
        outlet_a = Outlet.objects.create(tenant=tenant_a, name="A")
        tenant_b = Tenant.objects.create(name="Tenant B Fin")
        outlet_b = Outlet.objects.create(tenant=tenant_b, name="B")
        today = timezone.localdate()

        Expense.objects.create(
            tenant=tenant_b, outlet=outlet_b, category="rent",
            amount=Decimal("999.00"), expense_date=today,
        )

        from reports.services.pl_reports import net_profit_report
        result = net_profit_report(tenant_a, outlet_a, today, today)
        self.assertEqual(result["operating_expenses"], 0)
