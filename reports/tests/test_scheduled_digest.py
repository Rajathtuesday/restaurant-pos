# reports/tests/test_scheduled_digest.py
"""
Tests for the daily digest task and the ScheduledReportSubscription CRUD
endpoints. Delivery/plumbing, not new aggregation -- so unlike the P&L/labor/
menu-engineering tests, these check that the right things get sent, not
hand-derived numbers a second way (the numbers themselves are already
proven correct by test_finance.py / test_labor_report.py).

Run: python manage.py test reports.tests.test_scheduled_digest
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date, get_business_date_range
from orders.models import Order, Payment
from setup.models import ScheduledReportSubscription
from tenants.models import Outlet, Tenant, TenantFeatureOverride


class DailyDigestEmailTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Digest Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="digest_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.sub = ScheduledReportSubscription.objects.create(
            tenant=self.tenant, outlet=self.outlet,
            recipient_emails="a@example.com, b@example.com",
            created_by=self.owner,
        )

        # The task always digests "yesterday" -- create a paid order there.
        self.business_date = get_business_date(timezone.now(), self.outlet) - timedelta(days=1)
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
            status="paid", grand_total=Decimal("500.00"),
        )
        payment = Payment.objects.create(order=order, method="cash", amount=Decimal("500.00"), created_by=self.owner)
        range_start, _ = get_business_date_range(self.business_date, self.outlet)
        Payment.objects.filter(pk=payment.pk).update(paid_at=range_start + timedelta(hours=2))

    def test_sends_one_email_per_active_subscription_to_all_recipients(self):
        from reports.tasks import send_daily_digest_email
        sent = send_daily_digest_email()
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].to), {"a@example.com", "b@example.com"})

    def test_digest_body_contains_correct_sales_figure(self):
        from reports.tasks import send_daily_digest_email
        send_daily_digest_email()
        body = mail.outbox[0].body
        self.assertIn("Rs 500.00", body)
        self.assertIn("1 orders", body)

    def test_inactive_subscription_is_skipped(self):
        self.sub.is_active = False
        self.sub.save(update_fields=["is_active"])
        from reports.tasks import send_daily_digest_email
        sent = send_daily_digest_email()
        self.assertEqual(sent, 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_net_profit_and_labor_included_when_feature_enabled(self):
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="advanced_reports", enabled=True)
        from reports.tasks import send_daily_digest_email
        send_daily_digest_email()
        body = mail.outbox[0].body
        self.assertIn("Net profit", body)
        self.assertIn("Labor cost", body)

    def test_net_profit_omitted_when_feature_disabled(self):
        from reports.tasks import send_daily_digest_email
        send_daily_digest_email()
        body = mail.outbox[0].body
        self.assertNotIn("Net profit", body)


class ScheduledReportSubscriptionViewTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Sub Tenant", slug="sub-tenant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="sub_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.manager = User.objects.create_user(
            username="sub_manager", password="pw", role="manager",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.other_tenant = Tenant.objects.create(name="Other Sub Tenant", slug="other-sub-tenant")
        self.other_outlet = Outlet.objects.create(tenant=self.other_tenant, name="Other Main")
        self.other_owner = User.objects.create_user(
            username="other_sub_owner", password="pw", role="owner",
            tenant=self.other_tenant, outlet=self.other_outlet,
        )

    def test_owner_can_create_subscription(self):
        client = Client()
        client.force_login(self.owner)
        resp = client.post(
            reverse("report_subscription_create"),
            data=json.dumps({"recipient_emails": "x@example.com, y@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        sub = ScheduledReportSubscription.objects.get(tenant=self.tenant)
        self.assertEqual(sub.recipient_list, ["x@example.com", "y@example.com"])

    def test_manager_cannot_create_subscription(self):
        client = Client()
        client.force_login(self.manager)
        resp = client.post(
            reverse("report_subscription_create"),
            data=json.dumps({"recipient_emails": "x@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(ScheduledReportSubscription.objects.filter(tenant=self.tenant).exists())

    def test_invalid_email_rejected(self):
        client = Client()
        client.force_login(self.owner)
        resp = client.post(
            reverse("report_subscription_create"),
            data=json.dumps({"recipient_emails": "not-an-email"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_owner_can_toggle_and_delete(self):
        sub = ScheduledReportSubscription.objects.create(
            tenant=self.tenant, recipient_emails="x@example.com", created_by=self.owner,
        )
        client = Client()
        client.force_login(self.owner)

        resp = client.post(reverse("report_subscription_toggle", args=[sub.id]))
        self.assertEqual(resp.status_code, 200)
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)

        resp = client.post(reverse("report_subscription_delete", args=[sub.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ScheduledReportSubscription.objects.filter(id=sub.id).exists())

    def test_cannot_toggle_across_tenants(self):
        sub = ScheduledReportSubscription.objects.create(
            tenant=self.other_tenant, recipient_emails="x@example.com", created_by=self.other_owner,
        )
        client = Client()
        client.force_login(self.owner)
        resp = client.post(reverse("report_subscription_toggle", args=[sub.id]))
        self.assertEqual(resp.status_code, 404)
        sub.refresh_from_db()
        self.assertTrue(sub.is_active)
