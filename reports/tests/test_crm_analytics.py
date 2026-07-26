# reports/tests/test_crm_analytics.py
"""
Hand-calculated regression tests for crm_analytics_report().

Run: python manage.py test reports.tests.test_crm_analytics

created_at on Guest/LoyaltyTransaction/GuestFeedback is auto_now_add, so it
can't be set at .create() time -- rows that need to land "yesterday" are
created normally then backdated with a queryset .update(), which bypasses
auto_now_add (that only fires inside Model.save(), not QuerySet.update()).
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.utils import get_business_date
from crm.models import Guest, GuestFeedback, LoyaltyTransaction
from tenants.models import Outlet, Tenant


class RepeatRateTest(TestCase):
    """
    Guest A: 1 earn transaction, today only -> not a repeat (within period).
    Guest B: 2 earn transactions, both today -> a repeat within period.
    Guest C: 1 earn transaction today + 1 earn transaction BACKDATED to
             before the period -> only 1 counts as "in period", so C is
             NOT a repeat under the period-precise definition, even though
             C has 2 earn transactions in Guest lifetime history.

    active_guests = 3 (A, B, C each have >=1 earn txn in period)
    repeat_guests = 1 (B only)
    repeat_rate_pct = 1/3 * 100 = 33.3
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Repeat Rate Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.today = get_business_date(timezone.now(), self.outlet)

        self.guest_a = Guest.objects.create(tenant=self.tenant, phone="9000000001", name="A")
        self.guest_b = Guest.objects.create(tenant=self.tenant, phone="9000000002", name="B")
        self.guest_c = Guest.objects.create(tenant=self.tenant, phone="9000000003", name="C")

        LoyaltyTransaction.objects.create(guest=self.guest_a, transaction_type="earn", points=10)
        LoyaltyTransaction.objects.create(guest=self.guest_b, transaction_type="earn", points=10)
        LoyaltyTransaction.objects.create(guest=self.guest_b, transaction_type="earn", points=20)

        in_period_txn = LoyaltyTransaction.objects.create(guest=self.guest_c, transaction_type="earn", points=10)
        old_txn = LoyaltyTransaction.objects.create(guest=self.guest_c, transaction_type="earn", points=15)
        old_time = timezone.now() - timedelta(days=30)
        LoyaltyTransaction.objects.filter(pk=old_txn.pk).update(created_at=old_time)

    def test_period_precise_repeat_rate(self):
        from reports.services.crm_reports import crm_analytics_report
        report = crm_analytics_report(self.tenant, self.outlet, self.today, self.today)
        self.assertEqual(report["active_guests"], 3)
        self.assertEqual(report["repeat_guests"], 1)
        self.assertEqual(report["repeat_rate_pct"], 33.3)

    def test_cross_tenant_isolation(self):
        other_tenant = Tenant.objects.create(name="Other Repeat Tenant")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Other Main")
        from reports.services.crm_reports import crm_analytics_report
        report = crm_analytics_report(other_tenant, other_outlet, self.today, self.today)
        self.assertEqual(report["active_guests"], 0)
        self.assertEqual(report["repeat_rate_pct"], 0)


class LoyaltyTrendTest(TestCase):
    """
    Today: earn 100 + earn 50 -> Sum=150, count=2
    Yesterday (backdated): redeem -30 -> Sum=-30, count=1
    Query range: yesterday to today.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Loyalty Trend Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.today = get_business_date(timezone.now(), self.outlet)
        self.yesterday = self.today - timedelta(days=1)
        self.guest = Guest.objects.create(tenant=self.tenant, phone="9000000010", name="Loyal")

        LoyaltyTransaction.objects.create(guest=self.guest, transaction_type="earn", points=100)
        LoyaltyTransaction.objects.create(guest=self.guest, transaction_type="earn", points=50)

        redeem = LoyaltyTransaction.objects.create(guest=self.guest, transaction_type="redeem", points=-30)
        yesterday_time = timezone.now() - timedelta(days=1)
        LoyaltyTransaction.objects.filter(pk=redeem.pk).update(created_at=yesterday_time)

    def test_loyalty_trend_per_day_sums(self):
        from reports.services.crm_reports import crm_analytics_report
        report = crm_analytics_report(self.tenant, self.outlet, self.yesterday, self.today)
        by_day_type = {(row["day"], row["transaction_type"]): (row["points"], row["count"]) for row in report["loyalty_trend"]}
        today_entry = next(v for k, v in by_day_type.items() if k[0] == timezone.localdate() and k[1] == "earn")
        self.assertEqual(today_entry, (150, 2))


class FeedbackTrendTest(TestCase):
    """
    Today: ratings 4, 5 -> avg 4.5, count 2
    Yesterday (backdated): ratings 3, 3 -> avg 3.0, count 2
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Feedback Trend Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.today = get_business_date(timezone.now(), self.outlet)
        self.yesterday = self.today - timedelta(days=1)

        GuestFeedback.objects.create(tenant=self.tenant, outlet=self.outlet, guest_name="G1", rating=4)
        GuestFeedback.objects.create(tenant=self.tenant, outlet=self.outlet, guest_name="G2", rating=5)

        f3 = GuestFeedback.objects.create(tenant=self.tenant, outlet=self.outlet, guest_name="G3", rating=3)
        f4 = GuestFeedback.objects.create(tenant=self.tenant, outlet=self.outlet, guest_name="G4", rating=3)
        yesterday_time = timezone.now() - timedelta(days=1)
        GuestFeedback.objects.filter(pk__in=[f3.pk, f4.pk]).update(created_at=yesterday_time)

    def test_feedback_trend_per_day_averages(self):
        from reports.services.crm_reports import crm_analytics_report
        report = crm_analytics_report(self.tenant, self.outlet, self.yesterday, self.today)
        by_day = {row["day"]: (row["avg_rating"], row["count"]) for row in report["feedback_trend"]}
        today_avg, today_count = by_day[timezone.localdate()]
        self.assertEqual(today_count, 2)
        self.assertEqual(round(today_avg, 2), 4.5)

    def test_outlet_scoping_excludes_other_outlet(self):
        other_outlet = Outlet.objects.create(tenant=self.tenant, name="Other Branch")
        GuestFeedback.objects.create(tenant=self.tenant, outlet=other_outlet, guest_name="G5", rating=1)
        from reports.services.crm_reports import crm_analytics_report
        report = crm_analytics_report(self.tenant, self.outlet, self.yesterday, self.today)
        total_count = sum(row["count"] for row in report["feedback_trend"])
        self.assertEqual(total_count, 4)  # the other outlet's row must not be included
