"""
No GSTIN, no GST (P25). Only a business registered for GST may collect it
(CGST Act, section 32), and a tax invoice must show its GSTIN (CGST Rules,
rule 46). So an outlet without a valid GSTIN bills without GST, its bills
say "Bill" rather than "Tax Invoice", and every screen that sets the GSTIN
says so. A GSTIN that isn't one is never saved.

Two promises keep the past still:
  * bills totalled before the rule keep the GST they were billed with
  * an issued bill reads as it was issued, whatever the outlet changes later
    (the bill records its scheme: regular, composition or unregistered)

Run: python manage.py test orders.tests.test_gst_registration
"""
import json
from decimal import Decimal as D
from importlib import import_module

from django.apps import apps as django_apps
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.html import escape

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import Order, OrderItem, Table
from orders.scripts.demo_seed import create_or_reset_demo_tenant
from tenants.models import NO_GSTIN_MESSAGE, SAMPLE_GSTIN, Outlet, Tenant, read_gstin
from tenants.services.tenant_config_service import update_outlet_from_post


class GstinWorld(TestCase):
    """An outlet (with self.gstin, or none), its owner, a table and a 5% ₹100 curry."""
    gstin = None

    def setUp(self):
        self.tenant = Tenant.objects.create(name="GSTIN Test Cafe")
        self.outlet = Outlet.objects.create(tenant=self.tenant, name="Main", gst_no=self.gstin)
        self.owner = User.objects.create_user(
            username="gstin_owner", password="pw", role="owner", tenant=self.tenant, outlet=self.outlet,
        )
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Mains")
        self.curry = MenuItem.objects.create(
            tenant=self.tenant, outlet=self.outlet, category=category,
            name="Curry", price=D("100"), gst_percentage=D("5"),
        )
        self.table = Table.objects.create(tenant=self.tenant, outlet=self.outlet, name="T1")
        self.client = Client()
        self.client.force_login(self.owner)

    def order(self, quantity=2, parcel="0"):
        """An open order with `quantity` curries, not totalled yet."""
        order = Order.objects.create(
            tenant=self.tenant, outlet=self.outlet, table=self.table, created_by=self.owner,
            source="dine_in", status="open", parcel_surcharge=D(parcel),
            parcel_gst_rate=D("5") if parcel != "0" else None,
        )
        OrderItem.objects.create(
            order=order, menu_item=self.curry, quantity=quantity, price=self.curry.price,
            gst_percentage=self.curry.gst_percentage, total_price=self.curry.price * quantity,
            status="sent",
        )
        return order

    def bill(self, quantity=2, parcel="0"):
        order = self.order(quantity, parcel)
        order.recalculate_totals()
        return order

    def page(self, order):
        return self.client.get(reverse("bill-view", args=[order.id])).content.decode()


class NoGstinTest(GstinWorld):
    """An outlet with no GSTIN."""

    def test_a_new_bill_carries_no_gst(self):
        # 2 curries at ₹100 and a ₹20 parcel charge: no GST on any of it
        order = self.bill(2, parcel="20")
        self.assertEqual((order.gst_total, order.grand_total), (D("0.00"), D("220.00")))
        self.assertEqual(order.tax_rows(), [])
        self.assertEqual(order.parcel_tax, 0)
        self.assertEqual(order.gst_scheme, "unregistered")

    def test_a_gstin_that_isnt_one_counts_as_none(self):
        Outlet.objects.filter(pk=self.outlet.pk).update(gst_no="NA")
        self.outlet.refresh_from_db()
        self.assertFalse(self.outlet.is_gst_registered)
        self.assertEqual(self.bill().gst_total, D("0.00"))

    def test_the_bill_says_bill_not_tax_invoice(self):
        page = self.page(self.bill())
        self.assertIn('<span class="label">Bill</span>', page)
        self.assertNotIn("Tax Invoice", page)
        self.assertNotIn("CGST @", page)
        self.assertIn("No GST: this outlet has no GSTIN", page)

    def test_the_receipt_prints_no_gst_line(self):
        order = self.bill()
        receipt = self.client.get(reverse("thermal-receipt", args=[order.id])).content.decode()
        self.assertNotRegex(receipt, r'class="label( small)?">GST<')

    def test_the_gst_page_says_why(self):
        self.assertContains(self.client.get(reverse("gst_management")),
                            "This outlet has no GSTIN, so its bills carry no GST.")

    def test_the_settings_page_says_so_under_the_gstin_box(self):
        self.assertContains(self.client.get(reverse("outlet_settings")),
                            "No GSTIN, so bills carry no GST.")


class WithGstinTest(GstinWorld):
    gstin = SAMPLE_GSTIN

    def test_a_new_bill_carries_gst(self):
        # 2 x ₹100 at 5%: ₹10 GST on top
        order = self.bill(2)
        self.assertEqual((order.gst_total, order.grand_total), (D("10.00"), D("210.00")))
        self.assertEqual(order.gst_scheme, "regular")

    def test_a_gstin_saved_in_lowercase_is_still_the_gstin(self):
        # the save screens upper-case it, but one entered elsewhere (the admin) may not be
        Outlet.objects.filter(pk=self.outlet.pk).update(gst_no="29abcde1234f1z5")
        self.outlet.refresh_from_db()
        self.assertTrue(self.outlet.is_gst_registered)
        self.assertEqual(self.bill(2).gst_total, D("10.00"))

    def test_the_bill_is_a_tax_invoice_and_nobody_is_warned(self):
        page = self.page(self.bill())
        self.assertIn("Tax Invoice", page)
        self.assertNotIn("No GST: this outlet has no GSTIN", page)
        self.assertNotContains(self.client.get(reverse("gst_management")), "has no GSTIN")
        self.assertNotContains(self.client.get(reverse("outlet_settings")), "No GSTIN, so bills carry no GST.")

    def test_an_issued_bill_keeps_its_wording_when_the_gstin_goes(self):
        order = self.bill(2)
        Order.objects.filter(pk=order.pk).update(status="paid")
        Outlet.objects.filter(pk=self.outlet.pk).update(gst_no=None)
        order = Order.objects.get(pk=order.pk)
        self.assertEqual(order.gst_scheme, "regular")
        page = self.page(order)
        self.assertIn("Tax Invoice", page)
        self.assertIn("CGST @", page)


class BillsFromBeforeTheRuleTest(GstinWorld):
    """Before 28 September 2026 every outlet charged GST, GSTIN or not. Those
    bills have no tax record (or one without a scheme): they keep the GST
    they were billed with, in reports and on the page."""

    def test_an_old_bill_without_a_tax_record_keeps_its_gst(self):
        order = self.order(2)
        Order.objects.filter(pk=order.pk).update(
            status="paid", subtotal=D("200.00"), gst_total=D("10.00"), grand_total=D("210.00"),
        )
        order = Order.objects.get(pk=order.pk)
        self.assertIsNone(order.tax_summary)
        [row] = order.tax_rows()
        self.assertEqual((row.rate, row.taxable, row.tax), (D("5.00"), D("200.00"), D("10.00")))
        self.assertEqual(order.gst_scheme, "regular")
        self.assertIn("Tax Invoice", self.page(order))

    def test_a_record_from_before_the_scheme_was_kept_reads_as_it_was_billed(self):
        order = self.bill(2)
        record = {key: value for key, value in order.tax_summary.items() if key != "scheme"}
        Order.objects.filter(pk=order.pk).update(status="paid", tax_summary=record)
        self.assertEqual(Order.objects.get(pk=order.pk).gst_scheme, "regular")


class SavingTheGstinTest(GstinWorld):
    gstin = SAMPLE_GSTIN

    def settings_post(self, gst_no):
        return self.client.post(reverse("outlet_settings"), {
            "outlet_name": "Main", "gst_no": gst_no, "gst_inclusive": "false",
        }, follow=True)

    def test_reading_what_was_typed(self):
        self.assertEqual(read_gstin(""), (None, None))
        self.assertEqual(read_gstin(" 29abcde1234f1z5 "), ("29ABCDE1234F1Z5", None))
        gstin, message = read_gstin("NA")
        self.assertIsNone(gstin)
        self.assertIn("NA is not a valid GSTIN", message)

    def test_settings_keep_the_old_gstin_when_the_new_one_isnt_one(self):
        response = self.settings_post("12345")
        self.assertContains(response, "12345 is not a valid GSTIN, so it was not saved.")
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.gst_no, SAMPLE_GSTIN)

    def test_clearing_the_gstin_warns_that_bills_lose_gst(self):
        response = self.settings_post("")
        self.assertContains(response, escape(NO_GSTIN_MESSAGE))
        self.outlet.refresh_from_db()
        self.assertIsNone(self.outlet.gst_no)

    def test_the_superuser_and_portal_save_refuses_and_warns_too(self):
        self.assertIn("NA is not a valid GSTIN, so it was not saved.",
                      " ".join(update_outlet_from_post(self.outlet, {"gst_no": "NA"})))
        self.assertEqual(self.outlet.gst_no, SAMPLE_GSTIN)
        self.assertEqual(update_outlet_from_post(self.outlet, {"gst_no": ""}), [NO_GSTIN_MESSAGE])

    def test_a_new_restaurant_with_a_gstin_that_isnt_one_is_refused(self):
        superuser = User.objects.create_user(username="gstin_su", password="pw", is_superuser=True, is_staff=True)
        client = Client()
        client.force_login(superuser)
        response = client.post(reverse("superuser_create"), {
            "name": "Typo Cafe", "owner_username": "typo_owner", "owner_password": "pw", "gst_no": "29ABC",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("29ABC is not a valid GSTIN", json.loads(response.content)["error"])
        self.assertFalse(Tenant.objects.filter(name="Typo Cafe").exists())

    def test_onboarding_stays_on_step_one_with_the_reason(self):
        response = self.client.post("/setup/onboard/?step=1", {"name": "GSTIN Test Cafe", "gst_no": "BAD"})
        self.assertRedirects(response, "/setup/onboard/?step=1", fetch_redirect_response=False)
        self.assertContains(self.client.get("/setup/onboard/?step=1"), "BAD is not a valid GSTIN")
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.gst_no, SAMPLE_GSTIN)


class DemoRestaurantTest(TestCase):
    """The demo is there to show GST bills, so its outlet always has a GSTIN."""

    def test_the_deploy_gives_the_demo_its_gstin_and_nobody_else(self):
        migration = import_module("tenants.migrations.0036_demo_outlet_sample_gstin")
        self.assertEqual(migration.SAMPLE_GSTIN, SAMPLE_GSTIN)
        demo = Outlet.objects.create(tenant=Tenant.objects.create(name="Demo Bistro", slug="demo-bistro"),
                                     name="Demo Bistro - Main")
        cafe = Outlet.objects.create(tenant=Tenant.objects.create(name="Real Cafe"), name="Main")
        migration.give_the_demo_its_gstin(django_apps, None)
        demo.refresh_from_db()
        cafe.refresh_from_db()
        self.assertEqual((demo.gst_no, cafe.gst_no), (SAMPLE_GSTIN, None))

    def test_the_demo_outlet_gets_a_sample_gstin_back_on_every_reset(self):
        tenant = create_or_reset_demo_tenant()
        outlet = tenant.outlets.first()
        self.assertEqual(outlet.gst_no, SAMPLE_GSTIN)
        Outlet.objects.filter(pk=outlet.pk).update(gst_no=None)
        create_or_reset_demo_tenant()
        outlet.refresh_from_db()
        self.assertTrue(outlet.is_gst_registered)
