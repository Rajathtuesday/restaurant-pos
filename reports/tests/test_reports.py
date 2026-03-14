from django.test import TestCase
from tenants.models import Tenant, Outlet
from accounts.models import User
from orders.models import Order
from reports.services.sales_reports import daily_sales


class ReportsTest(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(name="Demo")

        self.outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="Main"
        )

        self.user = User.objects.create_user(
            username="owner",
            password="123",
            tenant=self.tenant,
            outlet=self.outlet,
            role="owner"
        )

        Order.objects.create(
            tenant=self.tenant,
            outlet=self.outlet,
            status="closed",
            grand_total=500
        )

    def test_daily_sales(self):

        result = daily_sales(self.tenant, self.outlet)

        self.assertEqual(result["total_sales"], 500)