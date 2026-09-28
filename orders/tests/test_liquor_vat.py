"""
Liquor on the bill (Phase 1 of md_files/liquor_vat_food_gst_plan_2026-09-27.html):
with the liquor_vat feature on, a drink with a liquor class is sold under
the state's VAT, never GST.

  * a bill line copies its tax when it is ordered (kind, rate, class name),
    so a later rate change never touches a bill already made
  * the bill's totals, tax record and sections keep food and liquor apart;
    Karnataka's class is 0% (no VAT at the bar since July 2017), and that
    liquor is still liquor, never nil-rated food
  * with the feature off nothing changes: every line is GST, as before
  * a Swiggy or Zomato order with liquor in it is refused whole
  * the menu sync gives a drink the target outlet's class of the same name,
    or leaves it unavailable rather than guess a tax
  * the GST Rates page never gives a drink a GST rate
  * D7: an outlet on the composition scheme can't sell liquor, and one that
    sells liquor can't go on composition, from any screen
  * a new liquor class starts at the state's rate where we know it
    (Karnataka: 0%) and asks for one elsewhere

The engine's arithmetic for liquor is in test_tax_engine.LiquorTest.

Run: python manage.py test orders.tests.test_liquor_vat
"""
import hashlib
import hmac
import json
from decimal import Decimal as D
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from menu.liquor import add_liquor_class, composition_refusal, default_liquor_vat_rate
from menu.models import COMPOSITION_SELLS_NO_LIQUOR, MenuCategory, MenuItem, VatClass
from orders.models import Order, OrderItem
from orders.services.order_service import add_items_to_order
from orders.services.tax_engine import GST, VAT, RateRow
from orders.services.tax_service import sale_tax_map
from orders.services.void_service import reduce_item_quantity
from setup.models import AggregatorConfig
from tenants.models import Outlet, Tenant, TenantFeatureOverride
from tenants.services.tenant_config_service import update_outlet_from_post

KARNATAKA_GSTIN = "29ABCDE1234F1Z5"
MAHARASHTRA_GSTIN = "27ABCDE1234F1Z5"


class PubBase(TestCase):
    """A Bengaluru pub with the liquor_vat feature: paneer tikka at 5% GST,
    a 0% Beer class (Karnataka's default) and a 5.5% Spirits class, the rate
    a state that charges VAT might use."""
    liquor_vat = True

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Copper Tap")
        if self.liquor_vat:
            TenantFeatureOverride.objects.create(tenant=self.tenant, feature="liquor_vat", enabled=True)
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Indiranagar", gst_no=KARNATAKA_GSTIN)
        self.owner = User.objects.create_user(
            username="copper_owner", password="pw", role="owner",
            tenant=self.tenant, outlet=self.outlet,
        )
        self.food = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.bar = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Bar")
        self.beer_class = add_liquor_class(self.outlet, "Beer")
        self.spirits_class = add_liquor_class(self.outlet, "Spirits", rate="5.5")
        self.paneer = self.dish("Paneer Tikka", "220", self.food, gst="5")
        self.pint = self.dish("Kingfisher Pint", "250", self.bar, vat_class=self.beer_class)
        self.rum = self.dish("Old Monk 60 ml", "220", self.bar, vat_class=self.spirits_class)

    def dish(self, name, price, category, gst="0", vat_class=None, outlet=None):
        return MenuItem.objects.create(
            tenant=self.tenant, outlet=outlet or self.outlet, category=category, name=name,
            price=D(price), gst_percentage=D(gst), vat_class=vat_class,
        )

    def order(self, *cart):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                     source="dine_in", status="open")
        add_items_to_order(self.owner, order, [{"id": item.id, "quantity": qty} for item, qty in cart])
        order.refresh_from_db()
        return order


class LineSnapshotTest(PubBase):

    def test_a_drink_is_sold_under_its_liquor_class(self):
        order = self.order((self.pint, 3), (self.paneer, 1))
        pint_line = order.items.get(menu_item=self.pint)
        self.assertEqual(
            (pint_line.tax_kind, pint_line.vat_rate, pint_line.vat_class_name, pint_line.gst_percentage),
            ("vat", D("0.00"), "Beer", D("0.00")),
        )
        paneer_line = order.items.get(menu_item=self.paneer)
        self.assertEqual((paneer_line.tax_kind, paneer_line.gst_percentage), ("gst", D("5.00")))

    def test_a_rate_change_later_never_touches_a_bill_already_made(self):
        order = self.order((self.rum, 2))
        self.assertEqual(order.vat_total, D("24.20"))          # 440 x 5.5%
        VatClass.objects.filter(pk=self.spirits_class.pk).update(rate=D("10"))

        add_items_to_order(self.owner, order, [{"id": self.paneer.id, "quantity": 1}])
        order.refresh_from_db()
        self.assertEqual(order.vat_total, D("24.20"))          # the rum keeps 5.5%
        new_rum = self.order((self.rum, 1))
        self.assertEqual(new_rum.vat_total, D("22.00"))        # a new order gets 10%

    def test_reducing_a_sent_drink_keeps_its_tax_on_both_lines(self):
        order = self.order((self.rum, 2))
        line = order.items.get()
        OrderItem.objects.filter(pk=line.pk).update(status="sent")

        reduce_item_quantity(self.owner, line.id, 1, "Guest changed their mind", made=False)

        voided = order.items.get(status="voided")
        self.assertEqual((voided.tax_kind, voided.vat_rate, voided.vat_class_name), ("vat", D("5.50"), "Spirits"))
        order.refresh_from_db()
        self.assertEqual(order.vat_total, D("12.10"))          # one rum left: 220 x 5.5%


class FeatureOffTest(PubBase):
    liquor_vat = False

    def test_with_the_feature_off_every_line_is_gst_as_before(self):
        order = self.order((self.pint, 3), (self.paneer, 1))
        self.assertEqual({line.tax_kind for line in order.items.all()}, {"gst"})
        self.assertEqual((order.gst_total, order.vat_total, order.grand_total), (D("11.00"), D("0.00"), D("981")))
        self.assertEqual([r.kind for r in order.tax_rows()], [GST, GST])

    def test_the_carts_are_told_the_same(self):
        taxes = sale_tax_map([self.pint, self.paneer], self.tenant)
        self.assertEqual(taxes[str(self.pint.id)], {"kind": "gst", "rate": "0.00"})
        self.assertEqual(taxes[str(self.paneer.id)], {"kind": "gst", "rate": "5.00"})


class PubBillTest(PubBase):

    def test_a_karnataka_pub_bill(self):
        order = self.order((self.paneer, 1), (self.pint, 3))
        # 220 + 11 GST + 750 beer, no tax on the beer
        self.assertEqual((order.gst_total, order.vat_total, order.grand_total), (D("11.00"), D("0.00"), D("981")))
        self.assertIn(RateRow(kind=VAT, rate=D("0.00"), taxable=D("750.00"), tax=D("0.00")), order.tax_rows())
        self.assertEqual([(s.kind, s.taxable, s.tax) for s in order.tax_sections()],
                         [(GST, D("220.00"), D("11.00")), (VAT, D("750.00"), D("0.00"))])
        # the bill's GST breakdown shows GST only
        self.assertEqual([row["rate"] for row in order.gst_breakdown], [D("5.00")])

    def test_vat_added_on_top_or_included_in_the_drink_price(self):
        on_top = self.order((self.rum, 2))
        self.assertEqual((on_top.vat_total, on_top.grand_total), (D("24.20"), D("464")))

        Outlet.objects.filter(pk=self.outlet.pk).update(vat_inclusive=True)
        inside = self.order((self.rum, 2))
        # 440 x 5.5 / 105.5 = 22.938..., inside the price the guest pays
        self.assertEqual((inside.vat_total, inside.grand_total), (D("22.94"), D("440")))

    def test_the_carts_are_told_each_drinks_tax(self):
        tenant = Tenant.objects.get(pk=self.tenant.pk)     # as a request has it: nothing cached yet
        with self.assertNumQueries(2):          # the feature's overrides, then the classes
            taxes = sale_tax_map([self.pint, self.rum, self.paneer], tenant)
        self.assertEqual(taxes, {
            str(self.pint.id): {"kind": "vat", "rate": "0.00"},
            str(self.rum.id): {"kind": "vat", "rate": "5.50"},
            str(self.paneer.id): {"kind": "gst", "rate": "5.00"},
        })


class AggregatorLiquorTest(PubBase):
    SECRET = "copper_tap_zomato"

    def setUp(self):
        super().setUp()
        AggregatorConfig.objects.create(tenant=self.tenant, outlet=self.outlet, zomato_enabled=True,
                                        zomato_webhook_secret=self.SECRET, auto_accept_orders=False)
        patcher = patch("orders.api.is_ip_allowed", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, *items):
        body = json.dumps({
            "tenant_id": self.tenant.id, "outlet_id": self.outlet.id, "source": "zomato",
            "aggregator_order_id": "ZMT-1",
            "items": [{"menu_item_id": item.id, "quantity": 1} for item in items],
        })
        signature = hmac.new(self.SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
        return self.client.post(reverse("api-ingest-order"), data=body, content_type="application/json",
                                HTTP_X_SIGNATURE=signature)

    def test_an_app_order_with_liquor_is_refused_whole(self):
        response = self.post(self.paneer, self.pint)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "Liquor can't be ordered through an aggregator: Kingfisher Pint")
        self.assertFalse(Order.objects.filter(tenant=self.tenant).exists())

    def test_an_app_order_of_food_still_goes_through(self):
        response = self.post(self.paneer)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Order.objects.get(tenant=self.tenant).items.get().tax_kind, "gst")


class MenuSyncTest(PubBase):

    def setUp(self):
        super().setUp()
        TenantFeatureOverride.objects.create(tenant=self.tenant, feature="multi_outlet", enabled=True)
        self.koramangala = Outlet.objects.create(tenant=self.tenant, name="Koramangala", gst_no=KARNATAKA_GSTIN)
        self.pune = Outlet.objects.create(tenant=self.tenant, name="Pune", gst_no=MAHARASHTRA_GSTIN)
        self.koramangala_beer = add_liquor_class(self.koramangala, "Beer")

    def test_a_drink_takes_the_target_outlets_class_of_the_same_name_or_waits(self):
        client = Client()
        client.force_login(self.owner)
        response = client.post(reverse("sync_menu_to_outlets"))
        self.assertEqual(response.status_code, 200, response.content)

        synced_pint = MenuItem.objects.get(outlet=self.koramangala, name="Kingfisher Pint")
        self.assertEqual((synced_pint.vat_class, synced_pint.is_available), (self.koramangala_beer, True))
        # no Spirits class at Koramangala, and no classes at all in Pune: those
        # drinks wait, unavailable, until someone gives them one
        for outlet, name in ((self.koramangala, "Old Monk 60 ml"), (self.pune, "Kingfisher Pint"),
                             (self.pune, "Old Monk 60 ml")):
            item = MenuItem.objects.get(outlet=outlet, name=name)
            self.assertEqual((item.vat_class, item.is_available), (None, False), (outlet.name, name))
        self.assertEqual(sorted(response.json()["stats"]["liquor_unclassified"]),
                         ["Koramangala: Old Monk 60 ml", "Pune: Kingfisher Pint", "Pune: Old Monk 60 ml"])


class GstRatesPageLiquorTest(PubBase):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.force_login(self.owner)

    def test_a_drink_is_never_given_a_gst_rate(self):
        response = self.client.post(reverse("update_item_gst", args=[self.pint.id]),
                                    json.dumps({"gst_percentage": "5.00"}), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("is liquor", response.json()["error"])
        self.pint.refresh_from_db()
        self.assertEqual(self.pint.gst_percentage, D("0.00"))

    def test_moving_a_whole_category_leaves_its_drinks_alone(self):
        self.dish("Masala Peanuts", "120", self.bar, gst="0")
        response = self.client.post(reverse("update_category_gst", args=[self.bar.id]),
                                    json.dumps({"gst_percentage": "5.00"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MenuItem.objects.get(name="Masala Peanuts").gst_percentage, D("5.00"))
        self.pint.refresh_from_db()
        self.assertEqual(self.pint.gst_percentage, D("0.00"))

    def test_the_page_shows_a_drinks_liquor_class_instead_of_a_gst_rate(self):
        response = self.client.get(reverse("gst_management"))
        self.assertContains(response, "Liquor: VAT Beer 0%, no GST")


class LiquorRulesTest(PubBase):

    def test_a_drink_with_a_liquor_class_carries_no_gst_in_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.dish("Mixed Drink", "300", self.bar, gst="5", vat_class=self.spirits_class)

    def test_a_drink_cant_use_another_outlets_class(self):
        other = Outlet.objects.create(tenant=self.tenant, name="Whitefield", gst_no=KARNATAKA_GSTIN)
        drink = MenuItem(tenant=self.tenant, outlet=other, category=self.bar, name="Pint",
                         price=D("250"), gst_percentage=D("0"), vat_class=self.beer_class)
        with self.assertRaisesMessage(ValidationError, "That VAT class belongs to another outlet."):
            drink.clean()

    def test_one_class_name_per_outlet_and_rates_from_0_to_100(self):
        with self.assertRaises(ValidationError):
            add_liquor_class(self.outlet, "Beer", rate="0")
        with self.assertRaises(ValidationError):
            add_liquor_class(self.outlet, "Wine", rate="101")
        with self.assertRaises(IntegrityError), transaction.atomic():
            VatClass.objects.create(tenant=self.tenant, outlet=self.outlet, name="Cider", rate=D("-1"))

    def test_a_new_class_starts_at_the_states_rate_where_we_know_it(self):
        self.assertEqual(default_liquor_vat_rate(self.outlet), D("0.00"))
        self.assertEqual(self.beer_class.rate, D("0.00"))

        pune = Outlet.objects.create(tenant=self.tenant, name="Pune", gst_no=MAHARASHTRA_GSTIN)
        no_gstin = Outlet.objects.create(tenant=self.tenant, name="Pop-up")
        self.assertIsNone(default_liquor_vat_rate(pune))
        self.assertIsNone(default_liquor_vat_rate(no_gstin))
        with self.assertRaisesMessage(ValidationError, "Enter the state's VAT rate on liquor for this outlet."):
            add_liquor_class(pune, "Beer")
        self.assertEqual(add_liquor_class(pune, "Beer", rate="10").rate, D("10.00"))


class CompositionGuardTest(PubBase):
    """D7: the composition scheme is closed to anyone selling liquor."""

    def test_an_outlet_on_composition_cant_get_a_liquor_class(self):
        cafe = Outlet.objects.create(tenant=self.tenant, name="Cafe", gst_no=KARNATAKA_GSTIN,
                                     is_composition_scheme=True)
        with self.assertRaisesMessage(ValidationError, COMPOSITION_SELLS_NO_LIQUOR):
            add_liquor_class(cafe, "Beer")
        with self.assertRaisesMessage(ValidationError, COMPOSITION_SELLS_NO_LIQUOR):
            VatClass(tenant=self.tenant, outlet=cafe, name="Beer", rate=D("0")).full_clean()

    def test_a_drink_cant_be_classified_at_an_outlet_on_composition(self):
        Outlet.objects.filter(pk=self.outlet.pk).update(is_composition_scheme=True)
        pint = MenuItem.objects.get(pk=self.pint.pk)       # with the outlet as it is now
        with self.assertRaisesMessage(ValidationError, COMPOSITION_SELLS_NO_LIQUOR):
            pint.clean()

    def test_an_outlet_selling_liquor_is_told_why_it_cant_go_on_composition(self):
        self.assertEqual(
            composition_refusal(self.outlet),
            "This outlet sells liquor (2 drinks have a liquor class), so it can't use the composition "
            "scheme: the law bars composition for anyone selling something outside GST.",
        )
        cafe = Outlet.objects.create(tenant=self.tenant, name="Cafe")
        self.assertIsNone(composition_refusal(cafe))

    def test_the_outlet_settings_screen_keeps_composition_off(self):
        client = Client()
        client.force_login(self.owner)
        response = client.post(reverse("outlet_settings"), {"is_composition_scheme": "on"}, follow=True)
        self.assertContains(response, "so it can&#x27;t use the composition scheme")
        self.outlet.refresh_from_db()
        self.assertFalse(self.outlet.is_composition_scheme)

    def test_the_superuser_portal_keeps_composition_off_too(self):
        notes = update_outlet_from_post(self.outlet, {"is_composition_scheme": "on"})
        self.assertEqual(notes, [composition_refusal(self.outlet)])
        self.outlet.refresh_from_db()
        self.assertFalse(self.outlet.is_composition_scheme)

        cafe = Outlet.objects.create(tenant=self.tenant, name="Cafe")
        self.assertEqual(update_outlet_from_post(cafe, {"is_composition_scheme": "on"}), [])
        cafe.refresh_from_db()
        self.assertTrue(cafe.is_composition_scheme)
