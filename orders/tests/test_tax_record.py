"""
The bill's tax record and the rules around it:

  * every bill stores what the tax engine worked out (Order.tax_summary), and
    the bill page's breakdown, returns and reports read that record
  * bills totalled before the record existed are worked out by the same engine
  * a paid or closed bill is never re-totalled
  * a bill's lines always come back in the order they were added
  * the parcel charge carries the outlet's parcel GST rate, copied onto the
    bill when the charge is turned on (P5), and outlets can set that rate

Run: python manage.py test orders.tests.test_tax_record
"""
from decimal import Decimal as D

from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import User
from menu.models import MenuCategory, MenuItem
from orders.models import IssuedBillError, Order, OrderItem
from orders.services.tax_engine import RateRow
from setup.models import PaymentConfig
from tenants.models import SAMPLE_GSTIN, Outlet, Tenant
from tenants.services.tenant_config_service import update_outlet_from_post


class Base(TestCase):
    """An outlet with a ₹10 flat parcel charge, a 5% dosa and an 18% sandwich."""
    inclusive = False
    composition = False

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tax Record Test")
        self.outlet = Outlet.objects.create(
            tenant=self.tenant, name="Main", gst_no=SAMPLE_GSTIN, gst_inclusive=self.inclusive,
            is_composition_scheme=self.composition,
            parcel_charge_amount=D("10"), parcel_charge_per_item=False,
        )
        PaymentConfig.objects.create(tenant=self.tenant, outlet=self.outlet, cash_enabled=True)
        self.owner = User.objects.create_user(
            username="tax_record_owner", password="x",
            tenant=self.tenant, outlet=self.outlet, role="owner",
        )
        category = MenuCategory.objects.create(tenant=self.tenant, outlet=self.outlet, name="Food")
        self.dosa = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                            name="Dosa", price=D("90"), gst_percentage=D("5"))
        self.sandwich = MenuItem.objects.create(tenant=self.tenant, outlet=self.outlet, category=category,
                                                name="Sandwich", price=D("320"), gst_percentage=D("18"))
        self.client = Client()
        self.client.force_login(self.owner)

    def order(self, *lines, status="open"):
        order = Order.objects.create(tenant=self.tenant, outlet=self.outlet, created_by=self.owner,
                                     source="counter", status=status)
        for item, qty in lines:
            OrderItem.objects.create(order=order, menu_item=item, quantity=qty, price=item.price,
                                     gst_percentage=item.gst_percentage, total_price=item.price * qty)
        order.recalculate_totals()
        return order

    def toggle_parcel(self, order):
        response = self.client.post(f"/toggle-parcel/{order.id}/")
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        return response.json()


class TaxRecordTest(Base):

    def test_every_bill_stores_its_tax_record(self):
        # 2 dosa 180 at 5% = 9.00; 1 sandwich 320 at 18% = 57.60
        order = self.order((self.dosa, 2), (self.sandwich, 1))
        order.refresh_from_db()
        self.assertEqual(order.tax_rows(), [
            RateRow("gst", D("5.00"), D("180.00"), D("9.00"), D("4.50"), D("4.50")),
            RateRow("gst", D("18.00"), D("320.00"), D("57.60"), D("28.80"), D("28.80")),
        ])
        self.assertEqual(order.gst_total, D("66.60"))

    def test_the_bill_page_breakdown_reads_the_record(self):
        order = self.order((self.dosa, 2), (self.sandwich, 1))
        order.refresh_from_db()
        first = order.gst_breakdown[0]
        self.assertEqual(
            (first["rate"], first["cgst_rate"], first["cgst_amount"], first["sgst_amount"], first["taxable"]),
            (D("5.00"), D("2.50"), D("4.50"), D("4.50"), D("180.00")),
        )
        # the old-shape cache is written too, so a rollback still shows it
        self.assertEqual(order.gst_breakdown_cache[1],
                         {"rate": "18.00", "cgst_rate": "9.00", "sgst_rate": "9.00",
                          "cgst_amount": "28.80", "sgst_amount": "28.80"})

    def test_a_bill_from_before_the_record_is_worked_out_by_the_same_engine(self):
        order = self.order((self.dosa, 3), (self.sandwich, 2))
        order.refresh_from_db()
        stored_rows, stored_gst = order.tax_rows(), order.gst_total
        Order.objects.filter(pk=order.pk).update(tax_summary=None)
        order.refresh_from_db()
        self.assertIsNone(order.tax_summary)
        self.assertEqual(order.tax_rows(), stored_rows)
        self.assertEqual(order.gst_total, stored_gst)

    def test_a_bill_from_before_the_record_shows_the_breakdown_it_was_saved_with(self):
        order = self.order((self.dosa, 1))
        old_shape = [{"rate": "5.00", "cgst_rate": "2.50", "sgst_rate": "2.50",
                      "cgst_amount": "2.25", "sgst_amount": "2.25"}]
        Order.objects.filter(pk=order.pk).update(tax_summary=None, gst_breakdown_cache=old_shape)
        order.refresh_from_db()
        self.assertEqual(order.gst_breakdown, [{k: D(v) for k, v in old_shape[0].items()}])

    def test_lines_come_back_in_the_order_they_were_added(self):
        order = self.order((self.sandwich, 1), (self.dosa, 1), (self.sandwich, 2))
        first = order.items.all()[0]
        first.status = "served"          # an update must not move a line
        first.save(update_fields=["status"])
        self.assertEqual([i.quantity for i in order.items.all()], [1, 1, 2])
        self.assertEqual([i.menu_item.name for i in order.items.all()], ["Sandwich", "Dosa", "Sandwich"])


class IssuedBillTest(Base):

    def test_a_paid_bill_is_never_re_totalled(self):
        order = self.order((self.dosa, 2))
        Order.objects.filter(pk=order.pk).update(status="paid")
        order.refresh_from_db()
        with self.assertRaises(IssuedBillError):
            order.recalculate_totals()
        order.refresh_from_db()
        self.assertEqual(order.grand_total, D("189"))

    def test_a_closed_bill_is_never_re_totalled(self):
        order = self.order((self.dosa, 2))
        Order.objects.filter(pk=order.pk).update(status="closed")
        order.refresh_from_db()
        with self.assertRaises(IssuedBillError):
            order.recalculate_totals()

    def test_a_bill_from_before_the_record_is_protected_too(self):
        order = self.order((self.dosa, 2))
        Order.objects.filter(pk=order.pk).update(status="paid", tax_summary=None)
        order.refresh_from_db()
        with self.assertRaises(IssuedBillError):
            order.recalculate_totals()

    def test_an_order_that_arrives_paid_is_totalled_once(self):
        # aggregator orders are created paid, then totalled
        order = self.order((self.dosa, 2), status="paid")
        self.assertEqual(order.grand_total, D("189"))
        with self.assertRaises(IssuedBillError):
            order.recalculate_totals()

    def test_open_billing_and_cancelled_bills_can_be_re_totalled(self):
        for status in ("open", "billing", "cancelled"):
            order = self.order((self.dosa, 1), status=status)
            order.recalculate_totals()
            self.assertEqual(order.grand_total, D("95"), status)


class ParcelGstTest(Base):
    """Prices exclude GST: the parcel's GST is added on top of the charge."""

    def test_turning_the_parcel_on_copies_the_outlets_rate(self):
        order = self.order((self.dosa, 2))                  # 180 + 9.00 GST
        data = self.toggle_parcel(order)
        self.assertEqual(order.parcel_gst_rate, D("5.00"))
        self.assertEqual(order.parcel_tax, D("0.50"))       # 5% of the ₹10 charge
        self.assertEqual(order.gst_total, D("9.50"))
        self.assertEqual(order.grand_total, D("200"))       # 180 + 9.50 + 10 = 199.50
        self.assertEqual(data["parcel_gst"], 0.5)

    def test_the_parcel_is_in_its_rates_row(self):
        order = self.order((self.dosa, 2))
        self.toggle_parcel(order)
        self.assertEqual(order.tax_rows()[0].taxable, D("190.00"))   # food 180 + parcel 10
        self.assertEqual(order.tax_rows()[0].tax, D("9.50"))

    def test_changing_the_outlet_rate_later_never_changes_the_bill(self):
        order = self.order((self.dosa, 2))
        self.toggle_parcel(order)
        self.outlet.parcel_gst_rate = D("18")
        self.outlet.save(update_fields=["parcel_gst_rate"])
        order.recalculate_totals()
        self.assertEqual(order.parcel_tax, D("0.50"))

    def test_turning_the_parcel_off_clears_its_rate_and_tax(self):
        order = self.order((self.dosa, 2))
        self.toggle_parcel(order)
        self.toggle_parcel(order)
        self.assertIsNone(order.parcel_gst_rate)
        self.assertEqual((order.parcel_tax, order.gst_total, order.grand_total), (D("0.00"), D("9.00"), D("189")))

    def test_an_outlet_at_18_percent(self):
        self.outlet.parcel_gst_rate = D("18")
        self.outlet.save(update_fields=["parcel_gst_rate"])
        order = self.order((self.dosa, 2))
        self.toggle_parcel(order)
        self.assertEqual(order.parcel_tax, D("1.80"))

    def test_a_parcel_set_without_a_rate_stays_untaxed(self):
        # a bill from before parcel GST: re-totalling keeps its charge untaxed
        order = self.order((self.dosa, 2))
        Order.objects.filter(pk=order.pk).update(parcel_surcharge=D("10"))
        order.refresh_from_db()
        order.recalculate_totals()
        self.assertEqual((order.parcel_tax, order.gst_total, order.grand_total), (D("0.00"), D("9.00"), D("199")))


class InclusiveParcelGstTest(Base):
    """Prices include GST: the parcel's GST is inside the charge."""
    inclusive = True

    def test_the_guest_pays_the_charge_and_the_gst_is_inside_it(self):
        order = self.order((self.dosa, 2))                  # 180 holds 8.57
        self.toggle_parcel(order)
        self.assertEqual(order.parcel_tax, D("0.48"))       # 10 x 5 / 105 = 0.476
        self.assertEqual(order.gst_total, D("9.05"))
        self.assertEqual(order.grand_total, D("190"))       # 180 + 10, nothing added
        self.assertEqual(order.subtotal, D("171.43"))       # the food's value before GST


class CompositionParcelTest(Base):
    composition = True

    def test_no_gst_on_the_parcel_and_no_rows(self):
        order = self.order((self.dosa, 2))
        self.toggle_parcel(order)
        self.assertEqual((order.parcel_tax, order.gst_total, order.grand_total), (D("0.00"), D("0.00"), D("190")))
        self.assertEqual(order.tax_rows(), [])
        self.assertEqual(order.gst_breakdown, [])


class ParcelRateSettingTest(Base):

    def _post_settings(self, rate):
        return self.client.post(reverse("outlet_settings"), {"outlet_name": "Main", "parcel_gst_rate": rate})

    def test_owner_sets_the_parcel_gst_rate(self):
        self._post_settings("18.00")
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.parcel_gst_rate, D("18.00"))

    def test_a_rate_that_is_not_a_gst_rate_is_ignored(self):
        for rate in ("12.00", "28.00", "abc"):
            self._post_settings(rate)
            self.outlet.refresh_from_db()
            self.assertEqual(self.outlet.parcel_gst_rate, D("5.00"), rate)

    def test_the_settings_page_offers_the_gst_rates(self):
        response = self.client.get(reverse("outlet_settings"))
        self.assertEqual([r["value"] for r in response.context["parcel_gst_rates"]], ["0.00", "5.00", "18.00"])

    def test_the_superuser_portal_saves_it_too(self):
        update_outlet_from_post(self.outlet, {"parcel_gst_rate": "0.00"})
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.parcel_gst_rate, D("0.00"))
        update_outlet_from_post(self.outlet, {"parcel_gst_rate": "28.00"})
        self.outlet.refresh_from_db()
        self.assertEqual(self.outlet.parcel_gst_rate, D("0.00"))
