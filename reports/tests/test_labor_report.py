# reports/tests/test_labor_report.py
"""
Hand-calculated regression tests for labor_cost_report().

Run: python manage.py test reports.tests.test_labor_report
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date
from orders.models import Order, Payment
from shifts.models import Shift, StaffPayRate
from tenants.models import Outlet, Tenant


class LaborCostReportTest(TestCase):
    """
    Two staff clock an 8-hour (hourly_staff) and 5-hour (unknown_staff)
    shift today. hourly_staff has a Rs100/hr rate on file + Rs50 tips ->
    cost = 8*100 + 50 = 850.00 exactly. unknown_staff has NO StaffPayRate
    row -> excluded entirely from total_labor_cost, not zero-padded.
    Rs1000 revenue -> labor_cost_pct = 850/1000*100 = 85.0.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Labor Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="labor_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.hourly_staff = User.objects.create_user(
            username="hourly_staff", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.unknown_staff = User.objects.create_user(
            username="unknown_staff", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )

        StaffPayRate.objects.create(
            tenant=self.tenant, staff=self.hourly_staff,
            pay_type="hourly", hourly_rate=Decimal("100.00"),
        )
        # unknown_staff deliberately has no StaffPayRate row.

        self.today = get_business_date(timezone.now(), self.outlet)
        now = timezone.now()
        Shift.objects.create(
            tenant=self.tenant, outlet=self.outlet, staff=self.hourly_staff,
            clocked_in_at=now, clocked_out_at=now + timedelta(hours=8),
            tips=Decimal("50.00"),
        )
        Shift.objects.create(
            tenant=self.tenant, outlet=self.outlet, staff=self.unknown_staff,
            clocked_in_at=now, clocked_out_at=now + timedelta(hours=5),
        )

        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
            status="paid", grand_total=Decimal("1000.00"),
        )
        Payment.objects.create(order=order, method="cash", amount=Decimal("1000.00"), created_by=self.owner)

    def _report(self):
        from reports.services.labor_reports import labor_cost_report
        return labor_cost_report(self.tenant, self.outlet, self.today, self.today)

    def test_hourly_cost_exact(self):
        row = next(r for r in self._report()["rows"] if r["username"] == "hourly_staff")
        self.assertEqual(row["cost"], 850.00)
        self.assertTrue(row["cost_known"])
        self.assertEqual(row["hours"], 8.0)

    def test_unknown_staff_excluded_from_total(self):
        result = self._report()
        row = next(r for r in result["rows"] if r["username"] == "unknown_staff")
        self.assertFalse(row["cost_known"])
        self.assertIsNone(row["cost"])
        self.assertEqual(result["staff_with_unknown_cost"], 1)
        # Total must be ONLY the known staff's cost -- not zero-padded unknown.
        self.assertEqual(result["total_labor_cost"], 850.00)

    def test_labor_cost_pct(self):
        result = self._report()
        self.assertEqual(result["labor_cost_pct"], 85.0)

    def test_cross_tenant_isolation(self):
        other_tenant = Tenant.objects.create(name="Other Labor Tenant")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other Main")
        from reports.services.labor_reports import labor_cost_report
        result = labor_cost_report(other_tenant, other_outlet, self.today, self.today)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["total_labor_cost"], 0)


class MonthlySalaryProrationTest(TestCase):
    """
    monthly_salary = Rs8680, period spans two different-length months:
      Jan 25-31 (7 of 31 days) -> 8680 * 7/31 = 1960.00
      Feb 1-5   (5 of 28 days, 2026 is not a leap year) -> 8680 * 5/28 = 1550.00
      Total = 3510.00 exactly.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Monthly Salary Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.staff = User.objects.create_user(
            username="salaried_staff", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        StaffPayRate.objects.create(
            tenant=self.tenant, staff=self.staff,
            pay_type="monthly", monthly_salary=Decimal("8680.00"),
        )

        self.start_date = date(2026, 1, 25)
        self.end_date = date(2026, 2, 5)

        # A single shift anywhere inside the period is enough for this staff
        # member to appear in the report at all -- the query is keyed off
        # Shift, not StaffPayRate.
        clock_in = timezone.make_aware(timezone.datetime(2026, 1, 28, 10, 0, 0))
        Shift.objects.create(
            tenant=self.tenant, outlet=self.outlet, staff=self.staff,
            clocked_in_at=clock_in, clocked_out_at=clock_in + timedelta(hours=6),
        )

    def test_prorate_across_month_boundary_exact(self):
        from reports.services.labor_reports import labor_cost_report
        result = labor_cost_report(self.tenant, self.outlet, self.start_date, self.end_date)
        row = next(r for r in result["rows"] if r["username"] == "salaried_staff")
        self.assertEqual(row["cost"], 3510.00)

    def test_prorate_helper_directly(self):
        from reports.services.labor_reports import _prorate_monthly_salary
        total = _prorate_monthly_salary(Decimal("8680.00"), self.start_date, self.end_date)
        self.assertEqual(total, Decimal("3510.00"))

    def test_single_month_period_exact(self):
        # Simple case first: exactly 10 days of a 30-day month.
        from reports.services.labor_reports import _prorate_monthly_salary
        total = _prorate_monthly_salary(
            Decimal("30000.00"), date(2026, 4, 1), date(2026, 4, 10),
        )
        # 30000 * 10/30 = 10000.00
        self.assertEqual(total, Decimal("10000.00"))


class EditPayRateViewTest(TestCase):
    """Regression tests for setup's edit_pay_rate endpoint."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="PayRate Tenant", slug="payrate-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="pr_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.cashier = User.objects.create_user(
            username="pr_cashier", password="pw", role="cashier",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.waiter = User.objects.create_user(
            username="pr_waiter", password="pw", role="waiter",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.other_tenant = Tenant.objects.create(name="PayRate Other", slug="payrate-other")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Other Main")
        self.other_waiter = User.objects.create_user(
            username="pr_other_waiter", password="pw", role="waiter",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )

    def _post(self, client, user_id, pay_type, amount):
        import json
        from django.urls import reverse
        return client.post(
            reverse("edit_pay_rate", args=[user_id]),
            data=json.dumps({"pay_type": pay_type, "amount": amount}),
            content_type="application/json",
        )

    def test_owner_can_set_hourly_rate(self):
        from django.test import Client
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "hourly", "150.00")
        self.assertEqual(resp.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.pay_rate.pay_type, "hourly")
        self.assertEqual(self.waiter.pay_rate.hourly_rate, Decimal("150.00"))

    def test_owner_can_update_existing_rate(self):
        from django.test import Client
        StaffPayRate.objects.create(
            tenant=self.tenant, staff=self.waiter, pay_type="hourly", hourly_rate=Decimal("100.00"),
        )
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "monthly", "20000.00")
        self.assertEqual(resp.status_code, 200)
        self.waiter.refresh_from_db()
        self.assertEqual(self.waiter.pay_rate.pay_type, "monthly")
        self.assertEqual(self.waiter.pay_rate.monthly_salary, Decimal("20000.00"))
        self.assertIsNone(self.waiter.pay_rate.hourly_rate)

    def test_cashier_cannot_set_pay_rate(self):
        from django.test import Client
        client = Client()
        client.force_login(self.cashier)
        resp = self._post(client, self.waiter.id, "hourly", "150.00")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(StaffPayRate.objects.filter(staff=self.waiter).exists())

    def test_cannot_set_negative_or_zero_amount(self):
        from django.test import Client
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.waiter.id, "hourly", "0")
        self.assertEqual(resp.status_code, 400)

    def test_cannot_set_owner_pay_rate(self):
        from django.test import Client
        second_owner = User.objects.create_user(
            username="pr_owner2", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, second_owner.id, "hourly", "150.00")
        self.assertEqual(resp.status_code, 403)

    def test_cannot_set_pay_rate_across_tenants(self):
        from django.test import Client
        client = Client()
        client.force_login(self.owner)
        resp = self._post(client, self.other_waiter.id, "hourly", "150.00")
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(StaffPayRate.objects.filter(staff=self.other_waiter).exists())
