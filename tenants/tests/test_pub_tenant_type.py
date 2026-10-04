"""
The "pub" tenant type: a Pub / Bar gets, by default, what a pub needs and
nothing a pub must not have, and no other tenant type changes.

  * tables, kitchen, QR menu, reservations (the fine-dining set) plus
    liquor_vat, which is off for every other type;
  * no composition scheme (a liquor seller can't use it);
  * staff land on the floor plan and billing screen like fine dining, not on
    the token screens of the counter types;
  * the setup panel can create one, and its preset matches the defaults.

Run: python manage.py test tenants.tests.test_pub_tenant_type
"""
from decimal import Decimal as D

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from accounts.views.auth_views import _role_path
from core.features import TENANT_FEATURES, has_feature
from menu.liquor import add_liquor_class
from menu.models import MenuCategory, MenuItem
from orders.models import Order
from orders.services.order_service import add_items_to_order
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant, TenantFeatureOverride
from tenants.services import tenant_config_service as tcs


_count = [0]


def make_tenant(tenant_type, name=None):
    _count[0] += 1
    tenant = Tenant.objects.create(name=name or f"{tenant_type} place {_count[0]}", tenant_type=tenant_type)
    outlet = Outlet.objects.create(tenant=tenant, name="Main", gst_no=SAMPLE_GSTIN)
    return tenant, outlet


class PubDefaultsTest(TestCase):

    def test_pub_is_a_tenant_type(self):
        self.assertIn("pub", Tenant.TenantType.values)
        self.assertEqual(Tenant.TenantType.PUB.label, "Pub / Bar")
        self.assertEqual(Tenant._meta.get_field("tenant_type").default, "fine_dining")   # unchanged

    def test_a_pub_has_tables_kitchen_qr_menu_and_liquor(self):
        tenant, _ = make_tenant("pub")
        for feature in ("floor_plan", "kot_system", "kitchen_display", "qr_menu", "split_bill",
                        "reservations", "inventory", "reports", "liquor_vat"):
            self.assertTrue(has_feature(tenant, feature), feature)

    def test_a_pub_never_has_the_composition_scheme_or_the_counter_screens_by_default(self):
        tenant, _ = make_tenant("pub")
        for feature in ("composition_scheme", "token_system", "direct_billing_mode", "counter_billing"):
            self.assertFalse(has_feature(tenant, feature), feature)

    def test_liquor_vat_is_on_for_a_pub_and_still_off_for_every_other_type(self):
        for tenant_type in ("fine_dining", "franchise", "cafe"):
            tenant, _ = make_tenant(tenant_type)
            self.assertFalse(has_feature(tenant, "liquor_vat"), tenant_type)
        self.assertEqual([t for t, features in TENANT_FEATURES.items() if "liquor_vat" in features], ["pub"])

    def test_a_pub_is_fine_dining_plus_liquor_and_offers_minus_the_composition_scheme(self):
        # Spelled out so a feature added to fine dining later is a decision for
        # pubs too, not an accident, and so nothing else drifts in or out.
        # Offers (offers/engine.py) joined on 4 Oct 2026, pubs only.
        self.assertEqual(
            set(TENANT_FEATURES["pub"]),
            (set(TENANT_FEATURES["fine_dining"]) - {"composition_scheme"}) | {"liquor_vat", "offers"},
        )
        self.assertFalse(any("offers" in TENANT_FEATURES[t] for t in TENANT_FEATURES if t != "pub"))

    def test_a_superuser_can_still_switch_liquor_off_for_one_pub(self):
        tenant, _ = make_tenant("pub")
        TenantFeatureOverride.objects.create(tenant=tenant, feature="liquor_vat", enabled=False)
        self.assertFalse(has_feature(tenant, "liquor_vat"))

    def test_the_preset_agrees_with_the_defaults(self):
        preset = tcs.PRESETS["pub"]
        self.assertLessEqual(set(preset["enable"]), set(TENANT_FEATURES["pub"]))
        self.assertIn("liquor_vat", preset["enable"])
        self.assertIn("composition_scheme", preset["disable"])
        self.assertTrue(set(preset["enable"]).isdisjoint(preset["disable"]))
        self.assertTrue(set(preset["disable"]).isdisjoint(TENANT_FEATURES["pub"]))


class PubBillsLikeAPubTest(TestCase):
    """The proof that matters: a pub created with no overrides at all bills
    its drinks under VAT, and the same cart at a fine-dining tenant stays GST."""

    def bill(self, tenant_type):
        tenant, outlet = make_tenant(tenant_type, f"Bill test {tenant_type}")
        user = User.objects.create_user(username=f"u_{tenant_type}", password="pw", role="owner",
                                        tenant=tenant, outlet=outlet)
        food = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Food")
        bar = MenuCategory.objects.create(tenant=tenant, outlet=outlet, name="Bar")
        paneer = MenuItem.objects.create(tenant=tenant, outlet=outlet, category=food, name="Paneer Tikka",
                                         price=D("220"), gst_percentage=D("5"))
        pint = MenuItem.objects.create(tenant=tenant, outlet=outlet, category=bar, name="Pint", price=D("250"),
                                       gst_percentage=D("0"), vat_class=add_liquor_class(outlet, "Beer"))
        order = Order.objects.create(tenant=tenant, outlet=outlet, created_by=user, source="dine_in", status="open")
        add_items_to_order(user, order, [{"id": paneer.id, "quantity": 1}, {"id": pint.id, "quantity": 3}])
        order.refresh_from_db()
        return order

    def test_a_pub_bills_drinks_under_vat_with_no_override(self):
        order = self.bill("pub")
        self.assertEqual({line.menu_item.name: line.tax_kind for line in order.items.all()},
                         {"Paneer Tikka": "gst", "Pint": "vat"})
        self.assertEqual((order.gst_total, order.vat_total, order.grand_total), (D("11.00"), D("0.00"), D("981")))

    def test_the_same_cart_at_fine_dining_stays_gst(self):
        order = self.bill("fine_dining")
        self.assertEqual({line.tax_kind for line in order.items.all()}, {"gst"})


class PubLandingTest(TestCase):

    def path(self, tenant_type, role):
        tenant, outlet = make_tenant(tenant_type)
        user = User.objects.create_user(username=f"{tenant_type}_{role}_{_count[0]}", password="pw", role=role,
                                        tenant=tenant, outlet=outlet)
        return _role_path(user)

    def test_pub_staff_land_on_tables_and_billing_like_fine_dining(self):
        for role, expected in (("waiter", "/tables/"), ("captain", "/tables/"), ("cashier", "/billing/"),
                               ("owner", "/dashboard/"), ("chef", "/kitchen/")):
            with self.subTest(role=role):
                self.assertEqual(self.path("pub", role), expected)
                self.assertEqual(self.path("fine_dining", role), expected)

    def test_the_counter_types_still_land_on_tokens(self):
        self.assertEqual(self.path("cafe", "waiter"), "/token/")
        self.assertEqual(self.path("franchise", "captain"), "/token/")
        self.assertEqual(self.path("cafe", "cashier"), "/token/")

    def test_a_user_without_a_tenant_still_gets_the_table_default(self):
        user = User(username="nobody", role="waiter", tenant=None)
        self.assertEqual(_role_path(user), "/tables/")

    def test_is_table_service(self):
        self.assertEqual({t: Tenant(tenant_type=t).is_table_service for t in Tenant.TenantType.values},
                         {"fine_dining": True, "pub": True, "franchise": False, "cafe": False})


class PubInThePanelTest(TestCase):

    def setUp(self):
        self.admin = User.objects.create_user(username="root_pub", password="pw", is_superuser=True, is_staff=True)
        self.client = Client()
        self.client.force_login(self.admin)

    def create(self, tenant_type, name="The Copper Tap"):
        return self.client.post(reverse("superuser_create"), {
            "name": name, "tenant_type": tenant_type, "owner_username": f"owner_{name[:6]}".replace(" ", ""),
            "owner_password": "pw12345", "gst_no": SAMPLE_GSTIN,
        })

    def test_the_form_offers_pub(self):
        page = self.client.get(reverse("superuser_panel")).content.decode()
        self.assertIn('<option value="pub">Pub / Bar</option>', page)

    def test_a_superuser_creates_a_pub(self):
        resp = self.create("pub")
        self.assertEqual(resp.status_code, 200, resp.content)
        tenant = Tenant.objects.get(name="The Copper Tap")
        self.assertEqual(tenant.tenant_type, "pub")
        self.assertTrue(has_feature(tenant, "liquor_vat"))

    def test_an_unknown_type_is_refused_not_stored(self):
        resp = self.create("brewery", name="Typo Place")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Tenant.objects.filter(name="Typo Place").exists())
