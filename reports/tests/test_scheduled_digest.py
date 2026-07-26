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
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.utils import get_business_date, get_business_date_range
from crm.models import Guest, LoyaltyTransaction
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderEvent, OrderItem, Payment
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


class EmailNotConfiguredTest(TestCase):
    """
    Mirrors notifications/services/whatsapp_service.py's graceful-degradation
    pattern: with the real SMTP backend selected but no credentials set
    (the state before mail is configured), the task must skip entirely --
    no network connection attempted, no exception, no retry -- rather than
    repeatedly opening a doomed SMTP connection once a day forever.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Unconfigured Mail Cafe")
        self.owner = User.objects.create_user(
            username="unconf_owner", password="pw", role="owner", tenant=self.tenant,
        )
        ScheduledReportSubscription.objects.create(
            tenant=self.tenant, recipient_emails="x@example.com", created_by=self.owner,
        )

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST_USER="", EMAIL_HOST_PASSWORD="",
    )
    def test_skips_entirely_with_no_smtp_credentials(self):
        # Mocking send_mail directly (not just checking the return value) is
        # the load-bearing part -- without the pre-flight check, the old code
        # still returns 0 here too, but only by accident (a from_email
        # validation error swallowed by its own try/except), having already
        # placed a real call. This proves no attempt was made at all.
        from unittest.mock import patch
        from reports.tasks import send_daily_digest_email
        with patch("reports.tasks.send_mail") as mock_send:
            sent = send_daily_digest_email()
        self.assertEqual(sent, 0)
        mock_send.assert_not_called()

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST_USER="rasova@gmail.com", EMAIL_HOST_PASSWORD="a-real-app-password",
    )
    def test_proceeds_once_smtp_credentials_are_set(self):
        # Doesn't actually reach the network -- there's no subscription with
        # sales data queued up here, this only proves the pre-flight check
        # itself passes and the function proceeds past it (returns 0 because
        # there's nothing to send, not because it was gated).
        from reports.tasks import _email_is_configured
        self.assertTrue(_email_is_configured())


class ExpandedDigestContentTest(TestCase):
    """
    "Feature-gated all the things" -- every report built this session gets a
    line in the digest when its feature is on: menu engineering, discount/
    void audit, and (additionally gated on the crm feature) repeat rate.
    """

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Expanded Digest Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        self.owner = User.objects.create_user(
            username="exp_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="advanced_reports", enabled=True)
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="crm", enabled=True)

        ScheduledReportSubscription.objects.create(
            tenant=self.tenant, recipient_emails="x@example.com", created_by=self.owner,
        )

        self.business_date = get_business_date(timezone.now(), self.outlet) - timedelta(days=1)
        range_start, _ = get_business_date_range(self.business_date, self.outlet)
        backdate = range_start + timedelta(hours=2)

        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        item = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=category,
            name="Curry", price=Decimal("200.00"), gst_percentage=Decimal("0"),
        )
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
            status="paid", grand_total=Decimal("200.00"),
        )
        Order.objects.filter(pk=order.pk).update(created_at=backdate)
        OrderItem.objects.create(
            order=order, menu_item=item, quantity=1, price=Decimal("200.00"),
            gst_percentage=Decimal("0"), total_price=Decimal("200.00"), status="pending",
        )
        payment = Payment.objects.create(order=order, method="cash", amount=Decimal("200.00"), created_by=self.owner)
        Payment.objects.filter(pk=payment.pk).update(paid_at=backdate)

        discount_event = OrderEvent.objects.create(
            tenant=self.tenant, outlet=self.outlet, order=order,
            event_type="discount_applied", metadata={"action": "discount_applied"}, created_by=self.owner,
        )
        OrderEvent.objects.filter(pk=discount_event.pk).update(created_at=backdate)

        guest = Guest.objects.create(tenant=self.tenant, phone="9000000099", name="Repeat Guest")
        earn_1 = LoyaltyTransaction.objects.create(guest=guest, transaction_type="earn", points=10)
        earn_2 = LoyaltyTransaction.objects.create(guest=guest, transaction_type="earn", points=10)
        LoyaltyTransaction.objects.filter(pk__in=[earn_1.pk, earn_2.pk]).update(created_at=backdate)

    def test_menu_mix_and_audit_lines_included(self):
        from reports.tasks import send_daily_digest_email
        send_daily_digest_email()
        body = mail.outbox[0].body
        self.assertIn("Menu mix:", body)
        self.assertIn("Discounts: 1, Voids: 0", body)

    def test_crm_repeat_rate_included_when_crm_feature_on(self):
        # 1 active guest with 2 earn transactions in-period -> 1/1 repeat = 100.0%
        from reports.tasks import send_daily_digest_email
        send_daily_digest_email()
        body = mail.outbox[0].body
        self.assertIn("Repeat customer rate: 100.0%", body)

    def test_crm_line_omitted_when_crm_feature_off(self):
        TenantFeatureOverride.objects.filter(tenant=self.tenant, feature="crm").update(enabled=False)
        from reports.tasks import send_daily_digest_email
        send_daily_digest_email()
        body = mail.outbox[0].body
        self.assertNotIn("Repeat customer rate", body)


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
