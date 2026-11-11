"""
pay_order looks at whose order it is before it reads the body.

Found by the tenant isolation walk (core/tests/test_tenant_walk.py) on
11 Nov 2026: pay_order parsed the JSON body and checked the payment
method before it looked the order up. So another restaurant's order id
answered 400 "Invalid payment method" (and, with a method given, which
methods this outlet takes) instead of 404, and a body that wasn't JSON
was a 500 logged as "Unexpected error". Both tests fail on the old view.

Run: python manage.py test orders.tests.test_pay_order_guards
"""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from core.testing import freeze_ratelimit_clock
from orders.models import Order
from setup.models import PaymentConfig
from tenants.models import Outlet, Tenant


class PayOrderGuardsTest(TestCase):
    def setUp(self):
        freeze_ratelimit_clock(self)
        # A counter outlet: pay_order opens a cash session by itself there,
        # so the happy path needs no shift fixture.
        self.tenant = Tenant.objects.create(name="Pay Guard A", slug="pay-guard-a", tenant_type="franchise")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main")
        PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet, cash_enabled=True)
        self.owner = User.objects.create_user(
            username="pay-guard-owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                          status="open", source="counter", grand_total=Decimal("100"))

        other_tenant = Tenant.objects.create(name="Pay Guard B", slug="pay-guard-b")
        other_outlet = Outlet.objects.create(tenant=other_tenant, name="Main")
        self.other_order = Order.objects.create(tenant=other_tenant, outlet=other_outlet,
                                                status="open", source="counter", grand_total=Decimal("100"))

        self.client = Client()
        self.client.force_login(self.owner)

    def test_another_restaurants_order_is_404_before_the_body_is_read(self):
        resp = self.client.post(
            reverse("pay-order", args=[self.other_order.id]),
            data='{"method": "upi", "amount": "100"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("method", resp.json()["error"].lower())

    def test_a_body_that_is_not_json_is_a_400_not_a_500(self):
        resp = self.client.post(
            reverse("pay-order", args=[self.order.id]),
            data="method=cash&amount=100", content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Invalid request body"})

    def test_a_json_list_is_a_400_too(self):
        resp = self.client.post(
            reverse("pay-order", args=[self.order.id]),
            data="[1, 2]", content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_own_order_with_a_good_body_still_pays(self):
        resp = self.client.post(
            reverse("pay-order", args=[self.order.id]),
            data='{"method": "cash", "amount": "100"}', content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content[:200])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "closed")
