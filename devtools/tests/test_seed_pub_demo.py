"""The demo pub seed (devtools seed_pub_demo) makes a pub the demo can run on,
and running it twice changes nothing more.

Run: python manage.py test devtools.tests.test_seed_pub_demo
"""
from decimal import Decimal as D
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from accounts.models import User
from core.features import has_feature
from inventory.kegs import keg_items
from menu.models import MenuItem
from offers.models import Offer
from orders.services import approvals
from setup.models import KitchenStation
from tenants.models import Tenant


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SeedPubDemoTest(TestCase):
    def test_a_ready_pub_and_safe_to_repeat(self):
        call_command("seed_pub_demo", stdout=StringIO())
        call_command("seed_pub_demo", stdout=StringIO())
        tenant = Tenant.objects.get(name="Copper Tap (demo)")
        outlet = tenant.outlets.get()
        self.assertEqual(tenant.tenant_type, "pub")
        self.assertTrue(has_feature(tenant, "cover_charge") and has_feature(tenant, "liquor_vat"))
        self.assertTrue(KitchenStation.objects.get(outlet=outlet, name="Bar").made_on_send)
        self.assertEqual(MenuItem.objects.filter(outlet=outlet).count(), 9)
        self.assertEqual(MenuItem.objects.get(outlet=outlet, name="Old Monk 30 ml").vat_class.rate, D("0.00"))
        self.assertEqual([i.name for i in keg_items(tenant, outlet)], ["Lager draught"])
        self.assertEqual(Offer.objects.filter(tenant=tenant).count(), 1)
        manager = User.objects.get(username="demo_pub_manager")
        self.assertEqual(approvals.check_pin(manager.id, "2580", tenant, outlet), manager)
