from django.test import TestCase
from tenants.models import Tenant, Outlet


class TenantModelTest(TestCase):

    def test_create_tenant(self):

        tenant = Tenant.objects.create(
            name="Pizza Hut",
            slug="pizza-hut"
        )

        self.assertEqual(tenant.name, "Pizza Hut")
        self.assertTrue(tenant.is_active)


class OutletModelTest(TestCase):

    def setUp(self):

        self.tenant = Tenant.objects.create(
            name="Dominos",
            slug="dominos"
        )

    def test_create_outlet(self):

        outlet = Outlet.objects.create(
            tenant=self.tenant,
            name="MG Road"
        )

        self.assertEqual(outlet.name, "MG Road")

    def test_unique_outlet_per_tenant(self):

        Outlet.objects.create(
            tenant=self.tenant,
            name="Indiranagar"
        )

        with self.assertRaises(Exception):

            Outlet.objects.create(
                tenant=self.tenant,
                name="Indiranagar"
            )