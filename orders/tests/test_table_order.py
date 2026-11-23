"""
Tables come in the order people count them, T1, T2 ... T10 (the walkthrough
recording, 23 Nov 2026: a 14-table floor plan ran T1, T10, T11 ... T14, T2,
because names were sorted as text). orders/services/table_order.py.

Run: python manage.py test orders.tests.test_table_order
"""
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from orders.models import Table
from orders.services.table_order import by_name, natural_key
from tenants.models import Outlet, Tenant

NAMES = ["T10", "T2", "T1", "T14", "T3", "B2", "B1"]
COUNTED = ["B1", "B2", "T1", "T2", "T3", "T10", "T14"]


class NaturalKeyTests(TestCase):

    def test_numbers_inside_names_count_as_numbers(self):
        self.assertEqual(sorted(NAMES, key=natural_key), COUNTED)
        self.assertEqual(sorted(["Table 10", "table 9", "Patio"], key=natural_key), ["Patio", "table 9", "Table 10"])


class TableListsTests(TestCase):

    def setUp(self):
        tenant = Tenant.objects.create(name="Counting Tables", tenant_type="fine_dining")
        outlet = Outlet.objects.create(tenant=tenant, name="Main")
        for name in NAMES:
            Table.objects.create(tenant=tenant, outlet=outlet, name=name, section="")
        self.owner = User.objects.create_user(username="count_owner", password="pw", tenant=tenant,
                                              outlet=outlet, role="owner")
        self.client.force_login(self.owner)

    def test_the_floor_plan_data_is_in_counting_order(self):
        data = self.client.get(reverse("tables-data")).json()
        rows = data["tables"] if isinstance(data, dict) else data
        self.assertEqual([t["name"] for t in rows], COUNTED)

    def test_the_free_tables_for_a_transfer_are_in_counting_order(self):
        data = self.client.get(reverse("available-tables")).json()
        self.assertEqual([t["name"] for t in data["tables"]], COUNTED)

    def test_by_name_takes_models_or_dicts(self):
        self.assertEqual([t.name for t in by_name(Table.objects.all())], COUNTED)
        self.assertEqual([t["name"] for t in by_name(Table.objects.values("name"))], COUNTED)
