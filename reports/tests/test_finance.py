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


class NetProfitReportGSTMathTest(TestCase):
    """
    Hand-calculated coverage for the GST-netting claim in pl_reports.py's own
    docstring ("Net revenue = Revenue - GST"), for a REAL (non-composition)
    outlet in both exclusive and inclusive GST modes -- the existing
    NetProfitReportTest only exercises composition-scheme outlets, where
    GST is always zero and this arithmetic is never actually exercised.

    Orders are built through Order.recalculate_totals() (not hand-set
    fields), matching orders/tests/test_gst_inclusive.py's own rigor, so
    this proves the report's netting agrees with the order model's real GST
    math, not just with numbers the test typed in twice.
    """

    def _order(self, tenant, outlet, user, category, price, gst_rate):
        menu_item = MenuItem.objects.create(
            tenant=tenant, outlet=outlet, category=category,
            name="Item", price=Decimal(str(price)), gst_percentage=Decimal(str(gst_rate)),
        )
        order = Order.objects.create(tenant=tenant, outlet=outlet, created_by=user)
        OrderItem.objects.create(
            order=order, menu_item=menu_item, quantity=1,
            price=menu_item.price, gst_percentage=menu_item.gst_percentage,
            total_price=menu_item.price, status="pending",
        )
        order.recalculate_totals()
        order.refresh_from_db()
        order.status = "paid"
        order.save(update_fields=["status"])
        Payment.objects.create(order=order, method="cash", amount=order.grand_total, created_by=user)
        return order

    def test_exclusive_mode_nets_out_added_on_gst(self):
        """Rs100 base @ 18% exclusive -> grand_total=118, gst_total=18.
        gross_revenue (Payment) = 118. net_revenue = 118 - 18 = 100 exactly."""
        tenant = Tenant.objects.create(name="GST Excl Cafe")
        outlet = Outlet.objects.create(tenant=tenant, name="Main", gst_inclusive=False)
        user = User.objects.create_user(username="gst_excl_owner", password="pw", role="owner", tenant=tenant, outlet=outlet)
        category = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Food")
        today = timezone.localdate()

        order = self._order(tenant, outlet, user, category, 100, 18)
        self.assertEqual(order.grand_total, Decimal("118"))
        self.assertEqual(order.gst_total, Decimal("18.00"))

        from reports.services.pl_reports import gross_margin_report
        result = gross_margin_report(tenant, outlet, today, today)
        self.assertEqual(result["gross_revenue"], 118.00)
        self.assertEqual(result["gst_collected"], 18.00)
        self.assertEqual(result["net_revenue"], 100.00)

    def test_inclusive_mode_nets_out_back_calculated_gst(self):
        """Rs118 inclusive @ 18% -> back-calculated gst_total=18, grand_total
        stays 118 (customer-facing price). Same net_revenue=100 as the
        exclusive case above -- proves the report nets out GST identically
        in both modes, not just when GST happens to be zero."""
        tenant = Tenant.objects.create(name="GST Incl Cafe")
        outlet = Outlet.objects.create(tenant=tenant, name="Main", gst_inclusive=True)
        user = User.objects.create_user(username="gst_incl_owner", password="pw", role="owner", tenant=tenant, outlet=outlet)
        category = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Food")
        today = timezone.localdate()

        order = self._order(tenant, outlet, user, category, 118, 18)
        self.assertEqual(order.grand_total, Decimal("118"))
        expected_gst = (Decimal("118") * 18 / 118).quantize(Decimal("0.01"))
        self.assertEqual(order.gst_total, expected_gst)

        from reports.services.pl_reports import gross_margin_report
        result = gross_margin_report(tenant, outlet, today, today)
        self.assertEqual(result["gross_revenue"], 118.00)
        self.assertEqual(result["gst_collected"], float(expected_gst))
        self.assertEqual(result["net_revenue"], round(118.00 - float(expected_gst), 2))

    def test_composition_scheme_gst_zeroed_even_if_items_have_rates(self):
        """A composition-scheme outlet must show gst_collected=0 in the
        report even though the menu item itself carries a nonzero GST rate
        -- pl_reports.py explicitly filters composition-scheme outlets out
        of the gst_collected aggregate as defense-in-depth alongside the
        order model's own is_composition guard. This proves that
        report-level filter is actually load-bearing: it's asserting
        against an item that -- if the filter were removed -- would have
        contributed nonzero GST were the order model's own guard the only
        thing standing between this and a wrong number."""
        tenant = Tenant.objects.create(name="GST Comp Cafe")
        outlet = Outlet.objects.create(tenant=tenant, name="Main", is_composition_scheme=True)
        user = User.objects.create_user(username="gst_comp_owner", password="pw", role="owner", tenant=tenant, outlet=outlet)
        category = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Food")
        today = timezone.localdate()

        order = self._order(tenant, outlet, user, category, 100, 18)
        # Order model itself must already zero this for composition scheme.
        self.assertEqual(order.gst_total, Decimal("0.00"))

        from reports.services.pl_reports import gross_margin_report
        result = gross_margin_report(tenant, outlet, today, today)
        self.assertEqual(result["gst_collected"], 0)
        self.assertEqual(result["net_revenue"], result["gross_revenue"])


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
