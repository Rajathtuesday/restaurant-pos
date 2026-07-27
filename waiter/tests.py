# waiter/tests.py
"""
Extracted from two orders/tests/ files (Phase 4 of the orders app split) --
each had exactly one WaiterCall-specific test method inside an otherwise
unrelated smoke-test class (both named POSTestCase, both walking through
create-order -> kitchen -> payment). Kept as two separate classes here,
matching their two separate origins, rather than merged into one.

Run: python manage.py test waiter
"""
from django.test import Client, TestCase

from accounts.models import User
from orders.models import Table
from tenants.models import Outlet, Tenant
from waiter.models import WaiterCall


class WaiterCallFromLegacyFlowTest(TestCase):
    """Moved from orders/tests/test_legacy.py::POSTestCase::test_waiter_call."""

    def setUp(self):
        self.client = Client()
        self.tenant = Tenant.objects.create(name="Demo Restaurant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Branch")
        self.user = User.objects.create_user(
            username="owner", password="1234",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.client.login(username="owner", password="1234")
        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Table 1"
        )

    def test_waiter_call(self):
        WaiterCall.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table
        )
        self.assertEqual(WaiterCall.objects.count(), 1)


class WaiterCallFromPosFlowTest(TestCase):
    """Moved from orders/tests/test_pos_flow.py::POSTestCase::test_waiter_call."""

    def setUp(self):
        self.client = Client()
        self.tenant = Tenant.objects.create(name="Demo Restaurant")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main Branch")
        self.user = User.objects.create_user(
            username="owner", password="1234",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        self.client.login(username="owner", password="1234")
        self.table = Table.objects.create(
            tenant=self.tenant, outlet=self.outlet, name="Table 1"
        )

    def test_waiter_call(self):
        WaiterCall.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table
        )
        self.assertEqual(WaiterCall.objects.count(), 1)
